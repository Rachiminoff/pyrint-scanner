#!/usr/bin/env python3
"""
epub_scanner.py - A production-quality EPUB illustration scanner and analyzer.

This tool scans EPUB files, detects illustrations, maps them to chapters/sections,
and exports results in JSON/CSV formats with optional image extraction and PDF catalog generation.
"""

import csv
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urlparse

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

# PDF generation imports
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import inch, mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Image as ReportLabImage,
        PageBreak, Table, TableStyle
    )
    from reportlab.pdfgen import canvas
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

# Constants
DEFAULT_OUTPUT_DIR = Path("output")
ICON_SIZE_THRESHOLD = (50, 50)
MIN_IMAGE_SIZE = (100, 100)

# Section/Chapter detection patterns
SECTION_PATTERNS = {
    "cover": [r"cover", r"front[_-]?cover", r"capa", r"cover\.xhtml"],
    "title_page": [r"title[_-]?page", r"titlepage", r"half[_-]?title"],
    "color_illustrations": [r"color[_-]?illustration", r"colored?[_-]?illustration", r"colorplate"],
    "illustrations": [r"illustration", r"plates?", r"figures?"],
    "front_matter": [r"front[_-]?matter", r"prelims?", r"front[_-]?pages?"],
    "prologue": [r"prologue", r"prolog", r"introduction", r"introdução"],
    "interlude": [r"interlude", r"intermezzo", r"between"],
    "side_story": [r"side[_-]?story", r"sidestory", r"extra[_-]?story"],
    "extra_story": [r"extra[_-]?story", r"bonus[_-]?story", r"special[_-]?story"],
    "bonus_chapter": [r"bonus[_-]?chapter", r"extra[_-]?chapter", r"special[_-]?chapter"],
    "epilogue": [r"epilogue", r"epilog", r"afterword", r"postscript"],
    "afterword": [r"afterword", r"postface", r"author[_-]?note"],
    "appendix": [r"appendix", r"appendices", r"supplement"],
    "short_story": [r"short[_-]?story", r"shortstory"],
}


@dataclass
class Illustration:
    """Represents an illustration found in an EPUB."""
    
    index: int
    section: str
    chapter: Optional[int]
    filename: str
    filepath: str
    content: Optional[bytes] = None
    width: Optional[int] = None
    height: Optional[int] = None
    is_duplicate: bool = False
    chapter_title: Optional[str] = None
    mime_type: Optional[str] = None


@dataclass
class Chapter:
    """Represents a chapter/section in an EPUB."""
    
    index: int
    title: str
    filename: str
    content: str
    illustrations: List[Illustration] = field(default_factory=list)
    is_section: bool = False
    section_type: str = "chapter"


class PDFGenerator:
    """Handles PDF catalog generation for illustrations."""
    
    def __init__(self, console: Console):
        self.console = console
        
    def _create_image_from_bytes(self, image_data: bytes, max_width: float = 400, max_height: float = 500) -> Optional[ReportLabImage]:
        """Create a ReportLab Image object from bytes data."""
        try:
            img_io = BytesIO(image_data)
            img = ReportLabImage(img_io, width=max_width, height=max_height)
            img._restrictSize(max_width, max_height)
            return img
        except Exception as e:
            return None
    
    def generate_catalog(self, result: Dict, output_path: Path, book_title: str) -> bool:
        """Generate a PDF catalog of illustrations with metadata."""
        if not PDF_SUPPORT:
            self.console.print("[red]PDF support not available. Install reportlab.[/]")
            return False
        
        try:
            illustrations = result.get("illustrations", [])
            if not illustrations:
                self.console.print("[yellow]No illustrations to include in PDF.[/]")
                return False
            
            unique_illustrations = [i for i in illustrations if not i.get("is_duplicate", False)]
            if not unique_illustrations:
                self.console.print("[yellow]No unique illustrations to include in PDF.[/]")
                return False
            
            doc = SimpleDocTemplate(
                str(output_path),
                pagesize=A4,
                rightMargin=50,
                leftMargin=50,
                topMargin=50,
                bottomMargin=50,
            )
            
            styles = getSampleStyleSheet()
            
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=22,
                alignment=TA_CENTER,
                spaceAfter=20,
                textColor=colors.HexColor('#1a1a2e')
            )
            
            heading_style = ParagraphStyle(
                'CustomHeading',
                parent=styles['Heading2'],
                fontSize=16,
                spaceAfter=12,
                textColor=colors.HexColor('#16213e')
            )
            
            body_style = ParagraphStyle(
                'CustomBody',
                parent=styles['Normal'],
                fontSize=10,
                spaceAfter=4,
                textColor=colors.HexColor('#2d2d2d')
            )
            
            metadata_style = ParagraphStyle(
                'Metadata',
                parent=styles['Normal'],
                fontSize=8,
                alignment=TA_LEFT,
                spaceAfter=2,
                textColor=colors.HexColor('#666666')
            )
            
            story = []
            
            story.append(Paragraph(f"<b>{book_title}</b>", title_style))
            story.append(Paragraph("Illustration Catalog", heading_style))
            story.append(Spacer(1, 0.1*inch))
            
            total_illus = len(illustrations)
            unique_count = len(unique_illustrations)
            story.append(Paragraph(f"Total Illustrations: {total_illus}", body_style))
            story.append(Paragraph(f"Unique Illustrations: {unique_count}", body_style))
            story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", metadata_style))
            story.append(Spacer(1, 0.3*inch))
            story.append(PageBreak())
            
            grouped_illustrations = {}
            for illus in unique_illustrations:
                chapter_title = illus.get("chapter_title") or illus.get("section", "Unknown Section")
                if chapter_title not in grouped_illustrations:
                    grouped_illustrations[chapter_title] = []
                grouped_illustrations[chapter_title].append(illus)
            
            for chapter_title, illus_list in grouped_illustrations.items():
                story.append(Paragraph(f"<b>{chapter_title}</b>", heading_style))
                story.append(Paragraph(f"{len(illus_list)} illustration(s)", metadata_style))
                story.append(Spacer(1, 0.1*inch))
                
                for i in range(0, len(illus_list), 2):
                    batch = illus_list[i:i+2]
                    table_data = []
                    
                    for illus in batch:
                        img_data = illus.get("content")
                        img_element = None
                        
                        if img_data:
                            img_element = self._create_image_from_bytes(img_data, max_width=250, max_height=350)
                        
                        if img_element is None:
                            img_element = Paragraph("[Image unavailable]", body_style)
                        
                        metadata_lines = []
                        metadata_lines.append(f"<b>#{illus.get('index', '?')}</b>")
                        if illus.get('width') and illus.get('height'):
                            metadata_lines.append(f"Size: {illus.get('width')}×{illus.get('height')}")
                        if illus.get('filename'):
                            metadata_lines.append(f"File: {illus.get('filename')}")
                        if illus.get('mime_type'):
                            metadata_lines.append(f"Type: {illus.get('mime_type')}")
                        
                        metadata_text = "<br/>".join(metadata_lines)
                        
                        container_data = [
                            [img_element],
                            [Paragraph(metadata_text, metadata_style)]
                        ]
                        
                        mini_table = Table(container_data, colWidths=[250])
                        mini_table.setStyle(TableStyle([
                            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('TOPPADDING', (0, 0), (-1, -1), 5),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                        ]))
                        
                        table_data.append(mini_table)
                    
                    if len(table_data) == 2:
                        row_table = Table([table_data], colWidths=[250, 250])
                        row_table.setStyle(TableStyle([
                            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('TOPPADDING', (0, 0), (-1, -1), 10),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
                            ('LEFTPADDING', (0, 0), (-1, -1), 5),
                            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                        ]))
                        story.append(row_table)
                    elif len(table_data) == 1:
                        story.append(table_data[0])
                    
                    story.append(Spacer(1, 0.1*inch))
                
                story.append(PageBreak())
            
            doc.build(story)
            self.console.print(f"[green]✓ PDF catalog generated: {output_path}[/]")
            return True
            
        except Exception as e:
            self.console.print(f"[red]Failed to generate PDF: {str(e)}[/]")
            import traceback
            self.console.print(f"[dim]{traceback.format_exc()}[/]")
            return False
    
    def generate_simple_catalog(self, result: Dict, output_path: Path, book_title: str) -> bool:
        """Generate a simpler PDF catalog with one illustration per page."""
        if not PDF_SUPPORT:
            return False
        
        try:
            illustrations = result.get("illustrations", [])
            if not illustrations:
                return False
            
            unique_illustrations = [i for i in illustrations if not i.get("is_duplicate", False)]
            if not unique_illustrations:
                return False
            
            doc = SimpleDocTemplate(
                str(output_path),
                pagesize=A4,
                rightMargin=50,
                leftMargin=50,
                topMargin=50,
                bottomMargin=50,
            )
            
            styles = getSampleStyleSheet()
            
            title_style = ParagraphStyle(
                'Title',
                parent=styles['Heading1'],
                fontSize=20,
                alignment=TA_CENTER,
                spaceAfter=20,
            )
            
            body_style = ParagraphStyle(
                'Body',
                parent=styles['Normal'],
                fontSize=10,
                spaceAfter=4,
            )
            
            metadata_style = ParagraphStyle(
                'Metadata',
                parent=styles['Normal'],
                fontSize=9,
                alignment=TA_CENTER,
                spaceAfter=2,
                textColor=colors.HexColor('#444444')
            )
            
            story = []
            
            story.append(Paragraph(f"<b>{book_title}</b>", title_style))
            story.append(Paragraph(f"Illustration Catalog - {len(unique_illustrations)} images", body_style))
            story.append(Spacer(1, 0.2*inch))
            story.append(PageBreak())
            
            for illus in unique_illustrations:
                img_data = illus.get("content")
                img_element = None
                
                if img_data:
                    img_element = self._create_image_from_bytes(img_data, max_width=450, max_height=550)
                
                if img_element is None:
                    img_element = Paragraph("[Image unavailable]", body_style)
                
                story.append(img_element)
                story.append(Spacer(1, 0.1*inch))
                
                metadata_lines = [
                    f"<b>Illustration #{illus.get('index', '?')}</b>",
                    f"Chapter: {illus.get('chapter_title', illus.get('section', 'Unknown'))}",
                    f"File: {illus.get('filename', 'Unknown')}",
                ]
                if illus.get('width') and illus.get('height'):
                    metadata_lines.append(f"Dimensions: {illus.get('width')}×{illus.get('height')}")
                if illus.get('mime_type'):
                    metadata_lines.append(f"Type: {illus.get('mime_type')}")
                
                for line in metadata_lines:
                    story.append(Paragraph(line, metadata_style))
                
                story.append(PageBreak())
            
            doc.build(story)
            self.console.print(f"[green]✓ Simple PDF catalog generated: {output_path}[/]")
            return True
            
        except Exception as e:
            self.console.print(f"[red]Failed to generate simple PDF: {str(e)}[/]")
            import traceback
            self.console.print(f"[dim]{traceback.format_exc()}[/]")
            return False


class TitleExtractor:
    """Handles intelligent title extraction from EPUB content."""
    
    # Patterns that indicate this is NOT a title
    NON_TITLE_PATTERNS = [
        r'^section\s*\d+',  # "Section 1"
        r'^part\s*\d+',     # "Part 1"
        r'^chapter\s*\d+',  # "Chapter 1" (without additional text)
        r'^vol\.?\s*\d+',   # "Vol. 1"
        r'^volume\s*\d+',   # "Volume 1"
        r'^\d+\s*[-:]\s*$', # Just a number
        r'^[-_=]{3,}$',     # Line of dashes/underscores
        r'^\s*$',           # Empty
    ]
    
    # Common title prefixes to strip
    TITLE_PREFIXES = [
        r'^(chapter|ch\.?|chap\.?)\s*\d+\s*[:.-]\s*',
        r'^(part|pt\.?)\s*\d+\s*[:.-]\s*',
        r'^(section|sec\.?)\s*\d+\s*[:.-]\s*',
        r'^(volume|vol\.?)\s*\d+\s*[:.-]\s*',
        r'^第\d+[章话節]\s*',
    ]
    
    # Decorative patterns that indicate chapter boundaries in light novels
    DECORATIVE_PATTERNS = [
        '☆☆☆', '***', '———', '〜〜〜', '◆◆◆', '✧✧✧', '✦✦✦',
        '❀❀❀', '✿✿✿', '🌸🌸🌸', '🌺🌺🌺', '🍀🍀🍀',
        '◎◎◎', '◉◉◉', '○●○', '♥♥♥', '♡♡♡',
    ]
    
    # Chapter keywords for detection
    CHAPTER_KEYWORDS = [
        'chapter', 'prologue', 'epilogue', 'afterword', 'appendix',
        'interlude', 'side story', 'bonus', 'extra', 'introduction',
        'preface', 'foreword', 'postscript', 'author\'s note',
    ]
    
    def __init__(self):
        self.debug = False
        
    def is_non_title(self, text: str) -> bool:
        """Check if text is clearly NOT a title."""
        if not text:
            return True
        
        text_lower = text.lower().strip()
        
        # Check against non-title patterns
        for pattern in self.NON_TITLE_PATTERNS:
            if re.search(pattern, text_lower):
                return True
        
        # Check if it's just a number
        if re.match(r'^[\d]+$', text_lower):
            return True
        
        # Check if it's just a single word that's not a chapter keyword
        words = text_lower.split()
        if len(words) == 1:
            word = words[0]
            # If it's a common word, probably not a title
            common_words = ['the', 'a', 'an', 'of', 'for', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'by']
            if word in common_words:
                return True
        
        return False
    
    def clean_title(self, text: str) -> str:
        """Clean and normalize a title."""
        if not text:
            return text
        
        # Remove common prefixes
        for prefix in self.TITLE_PREFIXES:
            text = re.sub(prefix, '', text, flags=re.IGNORECASE)
        
        # Remove trailing separators
        text = re.sub(r'\s*[—\-–:]\s*$', '', text)
        
        # Remove extra whitespace
        text = ' '.join(text.split())
        
        return text.strip()
    
    def extract_from_filename(self, filename: str) -> Optional[str]:
        """Extract title from filename."""
        name = os.path.splitext(os.path.basename(filename))[0]
        name = name.replace('_', ' ').replace('-', ' ')
        
        # Remove common prefixes
        name = re.sub(r'^(ch|chapter|chap|section|sec|part|pt|vol|volume)\s*\d+\s*[-:]\s*', '', name, flags=re.IGNORECASE)
        name = re.sub(r'^\d+\s*[-:]\s*', '', name)
        
        # Clean up
        name = ' '.join(name.split())
        
        if name and len(name) > 2 and not self.is_non_title(name):
            return name
        
        return None
    
    def extract_from_title_tag(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract title from <title> tag."""
        title_tag = soup.find('title')
        if not title_tag:
            return None
        
        text = title_tag.get_text(strip=True)
        if not text:
            return None
        
        # Remove common suffixes (like " | Novel | EPUB")
        text = re.sub(r'\s*[|—-]\s*.*$', '', text)
        text = re.sub(r'\s*[–—-]\s*.*$', '', text)
        
        # Remove prefixes
        for prefix in self.TITLE_PREFIXES:
            text = re.sub(prefix, '', text, flags=re.IGNORECASE)
        
        text = ' '.join(text.split())
        
        if text and not self.is_non_title(text):
            return self.clean_title(text)
        
        return None
    
    def extract_from_headings(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract title from heading tags."""
        for tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            for heading in soup.find_all(tag):
                text = heading.get_text(strip=True)
                if not text:
                    continue
                
                if len(text) < 200 and not self.is_non_title(text):
                    # Check if it contains chapter keywords
                    if any(keyword in text.lower() for keyword in self.CHAPTER_KEYWORDS):
                        return self.clean_title(text)
                    
                    # If it's a reasonable length and not non-title
                    if 3 < len(text) < 100:
                        return self.clean_title(text)
        
        return None
    
    def extract_from_decorative_patterns(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract title from decorative patterns common in light novels."""
        for pattern in self.DECORATIVE_PATTERNS:
            # Find elements containing the decorative pattern
            for elem in soup.find_all(string=re.compile(re.escape(pattern))):
                # Check nearby elements for title text
                parent = elem.parent
                if parent:
                    # Check next sibling
                    next_sib = parent.find_next_sibling()
                    if next_sib:
                        text = next_sib.get_text(strip=True)
                        if text and 3 < len(text) < 100 and not self.is_non_title(text):
                            return self.clean_title(text)
                    
                    # Check previous sibling
                    prev_sib = parent.find_previous_sibling()
                    if prev_sib:
                        text = prev_sib.get_text(strip=True)
                        if text and 3 < len(text) < 100 and not self.is_non_title(text):
                            return self.clean_title(text)
                    
                    # Check parent's next element
                    next_elem = parent.find_next()
                    if next_elem and next_elem.name in ['p', 'div']:
                        text = next_elem.get_text(strip=True)
                        if text and 3 < len(text) < 100 and not self.is_non_title(text):
                            return self.clean_title(text)
        
        return None
    
    def extract_from_centered_text(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract title from centered paragraphs."""
        # Look for paragraphs with center alignment
        for p in soup.find_all('p', style=re.compile(r'text-align:\s*center', re.I)):
            text = p.get_text(strip=True)
            if not text or len(text) > 100:
                continue
            
            if self.is_non_title(text):
                continue
            
            # Check if it looks like a chapter title
            if any(keyword in text.lower() for keyword in self.CHAPTER_KEYWORDS):
                return self.clean_title(text)
            
            # Check for chapter-like patterns
            if (re.search(r'^第', text) or  # Japanese chapter marker
                re.search(r'^\d+\.?\s*[—\-]\s*', text) or
                re.search(r'[A-Z][a-z]+\s+[A-Z][a-z]+', text)):
                return self.clean_title(text)
            
            # If it's short and centered, likely a title
            if len(text) < 50:
                return self.clean_title(text)
        
        return None
    
    def extract_from_class_patterns(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract title from elements with title-related classes."""
        class_patterns = [
            'title', 'chapter-title', 'section-title', 'heading', 'header',
            'chap', 'ch', 'subtitle', 'book-title', 'main-title'
        ]
        
        for elem in soup.find_all(['p', 'div', 'span']):
            classes = elem.get('class', [])
            if not classes:
                continue
            
            # Check if any class matches
            for class_name in classes:
                class_lower = class_name.lower()
                if any(pattern in class_lower for pattern in class_patterns):
                    text = elem.get_text(strip=True)
                    if text and 3 < len(text) < 100 and not self.is_non_title(text):
                        return self.clean_title(text)
        
        return None
    
    def extract_from_paragraph_keywords(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract title from paragraphs containing chapter keywords."""
        for p in soup.find_all('p'):
            text = p.get_text(strip=True)
            if not text or len(text) > 100:
                continue
            
            text_lower = text.lower()
            
            # Check for chapter keywords at the start
            for keyword in self.CHAPTER_KEYWORDS:
                if text_lower.startswith(keyword):
                    return self.clean_title(text)
            
            # Check for chapter keyword anywhere
            if any(keyword in text_lower for keyword in self.CHAPTER_KEYWORDS):
                # Only if it's not too long and not non-title
                if len(text) < 100 and not self.is_non_title(text):
                    return self.clean_title(text)
        
        return None
    
    def extract_title(self, soup: BeautifulSoup, filename: str, toc_title: str = "") -> Optional[str]:
        """Main method to extract title with multiple fallback strategies."""
        # Priority 1: TOC title (most reliable)
        if toc_title and not self.is_non_title(toc_title):
            return self.clean_title(toc_title)
        
        # Priority 2: Title tag
        title = self.extract_from_title_tag(soup)
        if title:
            return title
        
        # Priority 3: Headings
        title = self.extract_from_headings(soup)
        if title:
            return title
        
        # Priority 4: Decorative patterns (common in light novels)
        title = self.extract_from_decorative_patterns(soup)
        if title:
            return title
        
        # Priority 5: Centered text
        title = self.extract_from_centered_text(soup)
        if title:
            return title
        
        # Priority 6: Class-based detection
        title = self.extract_from_class_patterns(soup)
        if title:
            return title
        
        # Priority 7: Paragraph keywords
        title = self.extract_from_paragraph_keywords(soup)
        if title:
            return title
        
        # Priority 8: Filename
        title = self.extract_from_filename(filename)
        if title:
            return title
        
        return None


class EPUBScanner:
    """Main scanner class for processing EPUB files."""
    
    def __init__(self, console: Console):
        self.console = console
        self.scanned_books: List[Dict] = []
        self.total_time = 0.0
        self.total_illustrations = 0
        self.keep_duplicates = False
        self.extract_images = False
        self.generate_pdf = False
        self.pdf_style = "detailed"
        self.output_dir = DEFAULT_OUTPUT_DIR
        self.toc_titles: Dict[str, str] = {}
        self.pdf_generator = PDFGenerator(console)
        self.title_extractor = TitleExtractor()
        
    def process_epub(self, filepath: Path, progress_callback=None) -> Optional[Dict]:
        """Process a single EPUB file and return its data."""
        try:
            if progress_callback:
                progress_callback(f"Processing {filepath.name}...")
            
            book = epub.read_epub(str(filepath))
            title = self._get_title(book)
            self.toc_titles = self._build_toc_mapping(book)
            
            chapters, illustrations = self._process_reading_order(book)
            
            if not self.keep_duplicates:
                illustrations = self._deduplicate_illustrations(illustrations)
            
            illustrations.sort(key=lambda x: x.index)
            for idx, illus in enumerate(illustrations, 1):
                illus.index = idx
            
            self._assign_chapter_numbers(chapters)
            
            chapter_lookup = {ch.index: ch for ch in chapters if ch.index is not None}
            for illus in illustrations:
                if illus.chapter in chapter_lookup:
                    illus.chapter_title = chapter_lookup[illus.chapter].title
            
            result = {
                "filepath": str(filepath),
                "title": title,
                "chapters": [self._chapter_to_dict(c) for c in chapters],
                "illustrations": [self._illustration_to_dict(i) for i in illustrations],
                "illustration_count": len(illustrations)
            }
            
            if self.extract_images:
                self._extract_illustrations(filepath.stem, illustrations)
            
            if self.generate_pdf and PDF_SUPPORT:
                pdf_dir = self.output_dir / "pdf_catalogs"
                pdf_dir.mkdir(parents=True, exist_ok=True)
                pdf_path = pdf_dir / f"{filepath.stem}_catalog.pdf"
                
                if self.pdf_style == "simple":
                    self.pdf_generator.generate_simple_catalog(result, pdf_path, title)
                else:
                    self.pdf_generator.generate_catalog(result, pdf_path, title)
            
            return result
            
        except Exception as e:
            self.console.print(f"[red]✗ Error processing {filepath.name}: {str(e)}[/]")
            import traceback
            self.console.print(f"[dim]{traceback.format_exc()}[/]")
            return None
    
    def _get_title(self, book) -> str:
        """Extract the title from EPUB metadata."""
        try:
            metadata = book.get_metadata('DC', 'title')
            if metadata:
                return metadata[0][0]
        except:
            pass
        return "Unknown Title"
    
    def _build_toc_mapping(self, book) -> Dict[str, str]:
        """Build a mapping from filename to TOC title."""
        toc_map = {}
        try:
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_NAVIGATION:
                    content = item.get_content().decode('utf-8', errors='ignore')
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    for link in soup.find_all(['a', 'link']):
                        href = link.get('href')
                        text = link.get_text(strip=True)
                        if href and text:
                            href = href.split('#')[0]
                            if href:
                                toc_map[href] = text
        except:
            pass
        
        try:
            if hasattr(book, 'toc'):
                for toc_item in book.toc:
                    if hasattr(toc_item, 'href') and hasattr(toc_item, 'title'):
                        href = toc_item.href.split('#')[0] if toc_item.href else None
                        if href and toc_item.title:
                            toc_map[href] = toc_item.title
                    elif hasattr(toc_item, 'href') and hasattr(toc_item, 'text'):
                        href = toc_item.href.split('#')[0] if toc_item.href else None
                        if href and toc_item.text:
                            toc_map[href] = toc_item.text
        except:
            pass
        
        return toc_map
    
    def _process_reading_order(self, book) -> Tuple[List[Chapter], List[Illustration]]:
        """Process the EPUB in reading order."""
        chapters = []
        illustrations = []
        image_counter = 0
        
        spine_item_ids = []
        for spine_item in book.spine:
            if isinstance(spine_item, tuple):
                spine_item_ids.append(spine_item[0])
            elif isinstance(spine_item, str):
                spine_item_ids.append(spine_item)
        
        doc_items = {}
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                doc_items[item.get_id()] = item
        
        for item_id in spine_item_ids:
            if item_id in doc_items:
                item = doc_items[item_id]
                
                try:
                    content = item.get_content().decode('utf-8', errors='ignore')
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    toc_title = self.toc_titles.get(item.file_name, '')
                    
                    # Use the improved title extractor
                    section_title = self.title_extractor.extract_title(
                        soup, item.file_name, toc_title
                    )
                    
                    section_type = self._detect_section_type(soup, item.file_name, section_title)
                    
                    chapter = Chapter(
                        index=len(chapters),
                        title=section_title or f"Section {len(chapters) + 1}",
                        filename=item.file_name,
                        content=content,
                        is_section=section_type != "chapter",
                        section_type=section_type
                    )
                    
                    img_tags = soup.find_all(['img', 'image'])
                    for img in img_tags:
                        src = self._get_image_src(img)
                        if src and self._is_valid_illustration(img, src):
                            image_counter += 1
                            
                            img_content = self._get_image_content(book, src)
                            mime_type = self._detect_mime_type(src, img_content)
                            
                            illustration = Illustration(
                                index=image_counter,
                                section=section_title or section_type.capitalize(),
                                chapter=len(chapters) if section_type not in ["color_illustrations", "cover", "title_page"] else None,
                                filename=os.path.basename(src),
                                filepath=src,
                                content=img_content,
                                mime_type=mime_type
                            )
                            
                            self._get_image_dimensions(img, illustration)
                            
                            if not self.keep_duplicates:
                                for existing in illustrations:
                                    if self._are_duplicates(existing, illustration):
                                        illustration.is_duplicate = True
                                        break
                            
                            if not illustration.is_duplicate or self.keep_duplicates:
                                chapter.illustrations.append(illustration)
                                illustrations.append(illustration)
                    
                    chapters.append(chapter)
                    
                except Exception as e:
                    self.console.print(f"[yellow]Warning: Could not parse {item.file_name}: {str(e)}[/]")
                    continue
        
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT and item.get_id() not in spine_item_ids:
                if item.file_name and ('nav' in item.file_name.lower() or 'toc' in item.file_name.lower()):
                    continue
                
                try:
                    content = item.get_content().decode('utf-8', errors='ignore')
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    text = soup.get_text(strip=True)
                    if len(text) > 100:
                        toc_title = self.toc_titles.get(item.file_name, '')
                        section_title = self.title_extractor.extract_title(
                            soup, item.file_name, toc_title
                        )
                        section_type = self._detect_section_type(soup, item.file_name, section_title)
                        
                        chapter = Chapter(
                            index=len(chapters),
                            title=section_title or f"Section {len(chapters) + 1}",
                            filename=item.file_name,
                            content=content,
                            is_section=section_type != "chapter",
                            section_type=section_type
                        )
                        
                        chapters.append(chapter)
                except:
                    pass
        
        return chapters, illustrations
    
    def _detect_mime_type(self, filename: str, content: Optional[bytes] = None) -> Optional[str]:
        """Detect MIME type from filename or content."""
        ext = os.path.splitext(filename)[1].lower()
        mime_map = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp',
            '.svg': 'image/svg+xml',
            '.tiff': 'image/tiff',
            '.tif': 'image/tiff',
        }
        
        if ext in mime_map:
            return mime_map[ext]
        
        if content and len(content) > 8:
            if content[:8] == b'\x89PNG\r\n\x1a\n':
                return 'image/png'
            if content[:2] == b'\xff\xd8':
                return 'image/jpeg'
            if content[:3] == b'GIF':
                return 'image/gif'
            if content[:4] == b'RIFF' and content[8:12] == b'WEBP':
                return 'image/webp'
        
        return None
    
    def _detect_section_type(self, soup: BeautifulSoup, filename: str, title: Optional[str] = None) -> str:
        """Detect the type of section."""
        text = soup.get_text().lower()
        
        if re.search(r'cover|title page|half title', filename, re.IGNORECASE):
            return "cover"
        
        if re.search(r'color|colored?|colorplate', filename, re.IGNORECASE) or \
           (re.search(r'color', text, re.IGNORECASE) and re.search(r'illustration|plate', text, re.IGNORECASE)):
            return "color_illustrations"
        
        for section_type, patterns in SECTION_PATTERNS.items():
            for pattern in patterns:
                if title and re.search(pattern, title, re.IGNORECASE):
                    return section_type
                if re.search(pattern, filename, re.IGNORECASE):
                    return section_type
                if re.search(pattern, text, re.IGNORECASE):
                    return section_type
        
        if re.search(r'chapter\s*(\d+)', text, re.IGNORECASE) or \
           re.search(r'chapter\s*(\d+)', filename, re.IGNORECASE) or \
           (title and re.search(r'chapter\s*(\d+)', title, re.IGNORECASE)):
            return "chapter"
        
        if re.search(r'^\s*(?:\d+\.?\s*|第\d+[章话])\s*', text[:200]):
            return "chapter"
        
        if re.search(r'short story|side story|bonus|extra', text, re.IGNORECASE):
            return "extra"
        
        if re.search(r'afterword|postscript|author\'s note', text, re.IGNORECASE):
            return "afterword"
        
        return "chapter"
    
    def _assign_chapter_numbers(self, chapters: List[Chapter]) -> None:
        """Assign sequential numbers to chapters."""
        chapter_counter = 1
        for chapter in chapters:
            if chapter.section_type == "chapter" or chapter.section_type not in ["color_illustrations", "extra", "cover", "title_page", "afterword"]:
                chapter.index = chapter_counter
                chapter_counter += 1
            else:
                chapter.index = None
    
    def _get_image_src(self, img_tag) -> Optional[str]:
        """Extract src attribute from image tag."""
        if img_tag.name == 'img':
            return img_tag.get('src')
        elif img_tag.name == 'image':
            return img_tag.get('xlink:href') or img_tag.get('href')
        return None
    
    def _is_valid_illustration(self, img_tag, src: str) -> bool:
        """Determine if an image is a valid illustration."""
        if not src:
            return False
        
        icon_patterns = ['icon', 'logo', 'banner', 'spacer', 'dot', 'bullet', 'separator', 'btn', 'button']
        if any(pattern in src.lower() for pattern in icon_patterns):
            return False
        
        skip_extensions = ['.gif']
        if any(src.lower().endswith(ext) for ext in skip_extensions):
            return False
        
        width = img_tag.get('width')
        height = img_tag.get('height')
        if width and height:
            try:
                w, h = int(width), int(height)
                if w < MIN_IMAGE_SIZE[0] or h < MIN_IMAGE_SIZE[1]:
                    return False
            except:
                pass
        
        return True
    
    def _get_image_content(self, book, src: str) -> Optional[bytes]:
        """Retrieve image content from the EPUB."""
        try:
            parsed = urlparse(src)
            path = parsed.path
            if path.startswith('/'):
                path = path[1:]
            
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_IMAGE:
                    if item.file_name.endswith(path) or os.path.basename(item.file_name) == os.path.basename(path):
                        return item.get_content()
        except:
            pass
        return None
    
    def _get_image_dimensions(self, img_tag, illustration: Illustration) -> None:
        """Extract image dimensions if available."""
        width = img_tag.get('width')
        height = img_tag.get('height')
        if width and height:
            try:
                illustration.width = int(width)
                illustration.height = int(height)
            except:
                pass
    
    def _are_duplicates(self, illus1: Illustration, illus2: Illustration) -> bool:
        """Check if two illustrations are duplicates."""
        if illus1.filename == illus2.filename:
            return True
        
        if illus1.content and illus2.content:
            hash1 = hashlib.md5(illus1.content).hexdigest()
            hash2 = hashlib.md5(illus2.content).hexdigest()
            return hash1 == hash2
        
        return False
    
    def _deduplicate_illustrations(self, illustrations: List[Illustration]) -> List[Illustration]:
        """Remove duplicate illustrations."""
        seen = set()
        unique = []
        for illus in illustrations:
            key = illus.filename
            if illus.content:
                key = hashlib.md5(illus.content).hexdigest()
            if key not in seen:
                seen.add(key)
                unique.append(illus)
            else:
                illus.is_duplicate = True
        return unique
    
    def _extract_illustrations(self, book_title: str, illustrations: List[Illustration]) -> None:
        """Extract illustrations to the output directory."""
        output_path = self.output_dir / "extracted_images" / book_title
        output_path.mkdir(parents=True, exist_ok=True)
        
        for illus in illustrations:
            if illus.content:
                try:
                    ext = os.path.splitext(illus.filename)[1]
                    if not ext and illus.mime_type:
                        ext_map = {
                            'image/jpeg': '.jpg',
                            'image/png': '.png',
                            'image/gif': '.gif',
                            'image/webp': '.webp',
                            'image/svg+xml': '.svg',
                        }
                        ext = ext_map.get(illus.mime_type, '.jpg')
                    elif not ext:
                        ext = '.jpg'
                    
                    filename = f"{illus.index:03d}{ext}"
                    filepath = output_path / filename
                    
                    if not filepath.exists():
                        with open(filepath, 'wb') as f:
                            f.write(illus.content)
                except Exception as e:
                    self.console.print(f"[red]Could not extract {illus.filename}: {str(e)}[/]")
    
    def _chapter_to_dict(self, chapter: Chapter) -> Dict:
        """Convert Chapter to dictionary."""
        return {
            "index": chapter.index,
            "title": chapter.title,
            "filename": chapter.filename,
            "is_section": chapter.is_section,
            "section_type": chapter.section_type,
            "illustration_count": len(chapter.illustrations)
        }
    
    def _illustration_to_dict(self, illus: Illustration) -> Dict:
        """Convert Illustration to dictionary."""
        return {
            "index": illus.index,
            "section": illus.section,
            "chapter": illus.chapter,
            "chapter_title": illus.chapter_title,
            "filename": illus.filename,
            "filepath": illus.filepath,
            "width": illus.width,
            "height": illus.height,
            "mime_type": illus.mime_type,
            "is_duplicate": illus.is_duplicate,
            "content": illus.content
        }
    
    def export_json(self, result: Dict, output_dir: Path) -> None:
        """Export results to JSON."""
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(result["filepath"]).stem
        
        json_result = {
            "filepath": result["filepath"],
            "title": result["title"],
            "chapters": result["chapters"],
            "illustrations": []
        }
        
        for illus in result["illustrations"]:
            illus_copy = {k: v for k, v in illus.items() if k != "content"}
            json_result["illustrations"].append(illus_copy)
        
        json_path = output_dir / f"{filename}.json"
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_result, f, indent=2, ensure_ascii=False)
        
        self.console.print(f"[green]✓ JSON exported to {json_path}[/]")
    
    def export_csv(self, result: Dict, output_dir: Path) -> None:
        """Export results to CSV."""
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(result["filepath"]).stem
        csv_path = output_dir / f"{filename}.csv"
        
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Volume', 'Illustration', 'Section', 'Chapter', 'Chapter Title', 'Filename', 'Dimensions', 'MIME Type', 'Duplicate'])
            
            for illus in result["illustrations"]:
                chapter_num = illus["chapter"] if illus["chapter"] is not None else ''
                chapter_title = illus.get("chapter_title", '')
                dimensions = f"{illus.get('width', '')}×{illus.get('height', '')}" if illus.get('width') and illus.get('height') else ''
                mime_type = illus.get('mime_type', '')
                is_dup = 'Yes' if illus.get('is_duplicate', False) else 'No'
                
                writer.writerow([
                    result["title"],
                    illus["index"],
                    illus["section"],
                    chapter_num,
                    chapter_title,
                    illus["filename"],
                    dimensions,
                    mime_type,
                    is_dup
                ])
        
        self.console.print(f"[green]✓ CSV exported to {csv_path}[/]")
    
    def display_results(self, result: Dict) -> None:
        """Display results in a formatted table."""
        title = result.get("title", "Unknown Title")
        
        self.console.print()
        self.console.print(f"[bold cyan]═[/]" * 50)
        self.console.print(f"[bold] {title}[/]")
        self.console.print(f"[bold cyan]═[/]" * 50)
        
        chapters_with_illustrations = []
        for chapter in result["chapters"]:
            if chapter["illustration_count"] > 0:
                chapters_with_illustrations.append(chapter)
        
        if not chapters_with_illustrations:
            chapters_with_illustrations = result["chapters"]
        
        for chapter in chapters_with_illustrations:
            if chapter["illustration_count"] > 0:
                prefix = "✓" if not chapter["is_section"] else "◆"
                self.console.print(f"\n[green]{prefix} {chapter['title']}[/]")
                
                for illus in result["illustrations"]:
                    if illus["chapter"] == chapter["index"] or (illus["section"] == chapter["title"] and chapter["is_section"]):
                        if not illus["is_duplicate"] or self.keep_duplicates:
                            dup_marker = " [dim](duplicate)[/]" if illus["is_duplicate"] else ""
                            self.console.print(f"    [yellow]Illustration #{illus['index']}[/]{dup_marker}")
                            self.console.print(f"    [dim]{illus['filename']}[/]")
                            if illus.get('width') and illus.get('height'):
                                self.console.print(f"    [dim]{illus['width']}×{illus['height']}[/]")
            else:
                self.console.print(f"\n[dim]✓ {chapter['title']}[/]")
                self.console.print(f"    [dim](No illustrations)[/]")
        
        self.console.print()
        self.console.print(f"[bold cyan]─[/]" * 50)
        unique_count = len([i for i in result['illustrations'] if not i.get('is_duplicate', False)])
        self.console.print(f"[green]Total Illustrations: {result['illustration_count']}[/]")
        if not self.keep_duplicates:
            self.console.print(f"[green]Unique Illustrations: {unique_count}[/]")
        self.console.print()
    
    def display_summary(self) -> None:
        """Display final summary table."""
        if not self.scanned_books:
            self.console.print("[red]No books were scanned successfully.[/]")
            return
        
        table = Table(title="Scan Summary", show_header=True, header_style="bold magenta")
        table.add_column("EPUB", style="cyan", no_wrap=True)
        table.add_column("Images", justify="right", style="green")
        table.add_column("PDF", justify="center", style="yellow")
        
        total_images = 0
        for book in self.scanned_books:
            title = book.get("title", "Unknown")
            count = book.get("illustration_count", 0)
            has_pdf = "✓" if self.generate_pdf and PDF_SUPPORT else "—"
            table.add_row(title, str(count), has_pdf)
            total_images += count
        
        self.console.print(table)
        self.console.print()
        self.console.print(f"Books scanned: [cyan]{len(self.scanned_books)}[/]")
        self.console.print(f"Illustrations: [green]{total_images}[/]")
        self.console.print(f"Time elapsed : [yellow]{self.total_time:.2f} seconds[/]")
        if self.generate_pdf and PDF_SUPPORT:
            self.console.print(f"PDF catalogs: [yellow]Generated in {self.output_dir}/pdf_catalogs/[/]")
        if self.extract_images:
            self.console.print(f"Images extracted: [green]{self.output_dir}/extracted_images/[/]")


def main():
    """Main entry point for the application."""
    console = Console()
    scanner = EPUBScanner(console)
    
    welcome_text = Text()
    welcome_text.append("📚 ", style="bold cyan")
    welcome_text.append("EPUB Scanner", style="bold white")
    welcome_text.append("\nScan EPUB files and locate illustrations")
    welcome_text.append("\n\nMade for light novels", style="dim")
    
    console.print(Panel(welcome_text, title="Welcome", border_style="cyan"))
    
    console.print()
    choice = Prompt.ask(
        "What would you like to scan?",
        choices=["1", "2"],
        default="1",
        show_choices=True
    )
    
    if choice == "1":
        path_str = Prompt.ask("Enter the path to the EPUB file")
    else:
        path_str = Prompt.ask("Enter the path to the folder containing EPUB files")
    
    path_str = path_str.strip('"').strip("'")
    path = Path(path_str).expanduser().resolve()
    
    if not path.exists():
        console.print(f"[red]Error: Path does not exist: {path}[/]")
        sys.exit(1)
    
    scanner.keep_duplicates = Confirm.ask("Keep duplicate images?", default=False)
    scanner.extract_images = Confirm.ask("Extract illustrations?", default=False)
    
    if PDF_SUPPORT:
        scanner.generate_pdf = Confirm.ask("Generate PDF catalog of illustrations?", default=False)
        if scanner.generate_pdf:
            scanner.pdf_style = Prompt.ask(
                "PDF style",
                choices=["detailed", "simple"],
                default="detailed"
            )
            console.print(f"[dim]Using {scanner.pdf_style} PDF style (2 per page vs 1 per page)[/]")
    else:
        console.print("[yellow]PDF generation not available (install reportlab)[/]")
        scanner.generate_pdf = False
    
    if scanner.extract_images or scanner.generate_pdf:
        scanner.output_dir = Path(Prompt.ask("Output directory", default=str(DEFAULT_OUTPUT_DIR)))
    
    epub_files = []
    if path.is_file() and path.suffix.lower() == '.epub':
        epub_files = [path]
    elif path.is_dir():
        epub_files = sorted(path.rglob('*.epub'))
    
    if not epub_files:
        console.print("[red]No EPUB files found.[/]")
        sys.exit(1)
    
    start_time = time.time()
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Scanning EPUBs...", total=len(epub_files))
        
        def update_status(message):
            progress.update(task, description=message[:50])
        
        for epub_file in epub_files:
            result = scanner.process_epub(epub_file, progress_callback=update_status)
            if result:
                scanner.scanned_books.append(result)
                
                output_dir = scanner.output_dir / "data"
                scanner.export_json(result, output_dir)
                scanner.export_csv(result, output_dir)
                scanner.display_results(result)
            
            progress.advance(task)
    
    scanner.total_time = time.time() - start_time
    scanner.display_summary()


if __name__ == "__main__":
    main()