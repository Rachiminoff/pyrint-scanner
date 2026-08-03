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
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

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
    
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        
    def _log(self, message):
        if self.log_callback:
            self.log_callback(message)
        
    def _create_image_from_bytes(self, image_data: bytes, max_width: float = 400, max_height: float = 500) -> Optional[ReportLabImage]:
        try:
            img_io = BytesIO(image_data)
            img = ReportLabImage(img_io, width=max_width, height=max_height)
            img._restrictSize(max_width, max_height)
            return img
        except:
            return None
    
    def generate_catalog(self, result: Dict, output_path: Path, book_title: str) -> bool:
        if not PDF_SUPPORT:
            self._log("PDF support not available. Install reportlab.")
            return False
        
        try:
            illustrations = result.get("illustrations", [])
            if not illustrations:
                self._log("No illustrations to include in PDF.")
                return False
            
            unique_illustrations = [i for i in illustrations if not i.get("is_duplicate", False)]
            if not unique_illustrations:
                self._log("No unique illustrations to include in PDF.")
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
            self._log(f"✓ PDF catalog generated: {output_path}")
            return True
            
        except Exception as e:
            self._log(f"Failed to generate PDF: {str(e)}")
            import traceback
            self._log(f"{traceback.format_exc()}")
            return False
    
    def generate_simple_catalog(self, result: Dict, output_path: Path, book_title: str) -> bool:
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
            self._log(f"✓ Simple PDF catalog generated: {output_path}")
            return True
            
        except Exception as e:
            self._log(f"Failed to generate simple PDF: {str(e)}")
            import traceback
            self._log(f"{traceback.format_exc()}")
            return False


class TitleExtractor:
    """Handles intelligent title extraction from EPUB content."""
    
    NON_TITLE_PATTERNS = [
        r'^section\s*\d+',
        r'^part\s*\d+',
        r'^chapter\s*\d+',
        r'^vol\.?\s*\d+',
        r'^volume\s*\d+',
        r'^\d+\s*[-:]\s*$',
        r'^[-_=]{3,}$',
        r'^\s*$',
    ]
    
    TITLE_PREFIXES = [
        r'^(chapter|ch\.?|chap\.?)\s*\d+\s*[:.-]\s*',
        r'^(part|pt\.?)\s*\d+\s*[:.-]\s*',
        r'^(section|sec\.?)\s*\d+\s*[:.-]\s*',
        r'^(volume|vol\.?)\s*\d+\s*[:.-]\s*',
        r'^第\d+[章话節]\s*',
    ]
    
    DECORATIVE_PATTERNS = [
        '☆☆☆', '***', '———', '〜〜〜', '◆◆◆', '✧✧✧', '✦✦✦',
        '❀❀❀', '✿✿✿', '🌸🌸🌸', '🌺🌺🌺', '🍀🍀🍀',
        '◎◎◎', '◉◉◉', '○●○', '♥♥♥', '♡♡♡',
    ]
    
    CHAPTER_KEYWORDS = [
        'chapter', 'prologue', 'epilogue', 'afterword', 'appendix',
        'interlude', 'side story', 'bonus', 'extra', 'introduction',
        'preface', 'foreword', 'postscript', 'author\'s note',
    ]
    
    def __init__(self):
        self.debug = False
        
    def is_non_title(self, text: str) -> bool:
        if not text:
            return True
        text_lower = text.lower().strip()
        for pattern in self.NON_TITLE_PATTERNS:
            if re.search(pattern, text_lower):
                return True
        if re.match(r'^[\d]+$', text_lower):
            return True
        words = text_lower.split()
        if len(words) == 1:
            word = words[0]
            common_words = ['the', 'a', 'an', 'of', 'for', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'by']
            if word in common_words:
                return True
        return False
    
    def clean_title(self, text: str) -> str:
        if not text:
            return text
        for prefix in self.TITLE_PREFIXES:
            text = re.sub(prefix, '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*[—\-–:]\s*$', '', text)
        text = ' '.join(text.split())
        return text.strip()
    
    def extract_from_filename(self, filename: str) -> Optional[str]:
        name = os.path.splitext(os.path.basename(filename))[0]
        name = name.replace('_', ' ').replace('-', ' ')
        name = re.sub(r'^(ch|chapter|chap|section|sec|part|pt|vol|volume)\s*\d+\s*[-:]\s*', '', name, flags=re.IGNORECASE)
        name = re.sub(r'^\d+\s*[-:]\s*', '', name)
        name = ' '.join(name.split())
        if name and len(name) > 2 and not self.is_non_title(name):
            return name
        return None
    
    def extract_from_title_tag(self, soup: BeautifulSoup) -> Optional[str]:
        title_tag = soup.find('title')
        if not title_tag:
            return None
        text = title_tag.get_text(strip=True)
        if not text:
            return None
        text = re.sub(r'\s*[|—-]\s*.*$', '', text)
        text = re.sub(r'\s*[–—-]\s*.*$', '', text)
        for prefix in self.TITLE_PREFIXES:
            text = re.sub(prefix, '', text, flags=re.IGNORECASE)
        text = ' '.join(text.split())
        if text and not self.is_non_title(text):
            return self.clean_title(text)
        return None
    
    def extract_from_headings(self, soup: BeautifulSoup) -> Optional[str]:
        for tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            for heading in soup.find_all(tag):
                text = heading.get_text(strip=True)
                if not text:
                    continue
                if len(text) < 200 and not self.is_non_title(text):
                    if any(keyword in text.lower() for keyword in self.CHAPTER_KEYWORDS):
                        return self.clean_title(text)
                    if 3 < len(text) < 100:
                        return self.clean_title(text)
        return None
    
    def extract_from_decorative_patterns(self, soup: BeautifulSoup) -> Optional[str]:
        for pattern in self.DECORATIVE_PATTERNS:
            for elem in soup.find_all(string=re.compile(re.escape(pattern))):
                parent = elem.parent
                if parent:
                    next_sib = parent.find_next_sibling()
                    if next_sib:
                        text = next_sib.get_text(strip=True)
                        if text and 3 < len(text) < 100 and not self.is_non_title(text):
                            return self.clean_title(text)
                    prev_sib = parent.find_previous_sibling()
                    if prev_sib:
                        text = prev_sib.get_text(strip=True)
                        if text and 3 < len(text) < 100 and not self.is_non_title(text):
                            return self.clean_title(text)
                    next_elem = parent.find_next()
                    if next_elem and next_elem.name in ['p', 'div']:
                        text = next_elem.get_text(strip=True)
                        if text and 3 < len(text) < 100 and not self.is_non_title(text):
                            return self.clean_title(text)
        return None
    
    def extract_from_centered_text(self, soup: BeautifulSoup) -> Optional[str]:
        for p in soup.find_all('p', style=re.compile(r'text-align:\s*center', re.I)):
            text = p.get_text(strip=True)
            if not text or len(text) > 100:
                continue
            if self.is_non_title(text):
                continue
            if any(keyword in text.lower() for keyword in self.CHAPTER_KEYWORDS):
                return self.clean_title(text)
            if (re.search(r'^第', text) or
                re.search(r'^\d+\.?\s*[—\-]\s*', text) or
                re.search(r'[A-Z][a-z]+\s+[A-Z][a-z]+', text)):
                return self.clean_title(text)
            if len(text) < 50:
                return self.clean_title(text)
        return None
    
    def extract_from_class_patterns(self, soup: BeautifulSoup) -> Optional[str]:
        class_patterns = [
            'title', 'chapter-title', 'section-title', 'heading', 'header',
            'chap', 'ch', 'subtitle', 'book-title', 'main-title'
        ]
        for elem in soup.find_all(['p', 'div', 'span']):
            classes = elem.get('class', [])
            if not classes:
                continue
            for class_name in classes:
                class_lower = class_name.lower()
                if any(pattern in class_lower for pattern in class_patterns):
                    text = elem.get_text(strip=True)
                    if text and 3 < len(text) < 100 and not self.is_non_title(text):
                        return self.clean_title(text)
        return None
    
    def extract_from_paragraph_keywords(self, soup: BeautifulSoup) -> Optional[str]:
        for p in soup.find_all('p'):
            text = p.get_text(strip=True)
            if not text or len(text) > 100:
                continue
            text_lower = text.lower()
            for keyword in self.CHAPTER_KEYWORDS:
                if text_lower.startswith(keyword):
                    return self.clean_title(text)
            if any(keyword in text_lower for keyword in self.CHAPTER_KEYWORDS):
                if len(text) < 100 and not self.is_non_title(text):
                    return self.clean_title(text)
        return None
    
    def extract_title(self, soup: BeautifulSoup, filename: str, toc_title: str = "") -> Optional[str]:
        if toc_title and not self.is_non_title(toc_title):
            return self.clean_title(toc_title)
        title = self.extract_from_title_tag(soup)
        if title:
            return title
        title = self.extract_from_headings(soup)
        if title:
            return title
        title = self.extract_from_decorative_patterns(soup)
        if title:
            return title
        title = self.extract_from_centered_text(soup)
        if title:
            return title
        title = self.extract_from_class_patterns(soup)
        if title:
            return title
        title = self.extract_from_paragraph_keywords(soup)
        if title:
            return title
        title = self.extract_from_filename(filename)
        if title:
            return title
        return None


class EPUBScanner:
    """Main scanner class for processing EPUB files."""
    
    def __init__(self, log_callback=None, progress_callback=None):
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.scanned_books: List[Dict] = []
        self.total_time = 0.0
        self.total_illustrations = 0
        self.keep_duplicates = False
        self.extract_images = False
        self.generate_pdf = False
        self.pdf_style = "detailed"
        self.output_dir = DEFAULT_OUTPUT_DIR
        self.toc_titles: Dict[str, str] = {}
        self.pdf_generator = PDFGenerator(log_callback)
        self.title_extractor = TitleExtractor()
        self.should_stop = False
        
    def _log(self, message):
        if self.log_callback:
            self.log_callback(message)
        
    def _update_progress(self, message, value=None):
        if self.progress_callback:
            self.progress_callback(message, value)
        
    def stop(self):
        self.should_stop = True
        
    def process_epub(self, filepath: Path) -> Optional[Dict]:
        try:
            self._update_progress(f"Processing {filepath.name}...")
            
            book = epub.read_epub(str(filepath))
            title = self._get_title(book)
            self.toc_titles = self._build_toc_mapping(book)
            
            chapters, illustrations = self._process_reading_order(book)
            
            if self.should_stop:
                return None
            
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
            self._log(f"✗ Error processing {filepath.name}: {str(e)}")
            import traceback
            self._log(f"{traceback.format_exc()}")
            return None
    
    def _get_title(self, book) -> str:
        try:
            metadata = book.get_metadata('DC', 'title')
            if metadata:
                return metadata[0][0]
        except:
            pass
        return "Unknown Title"
    
    def _build_toc_mapping(self, book) -> Dict[str, str]:
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
                    self._log(f"Warning: Could not parse {item.file_name}: {str(e)}")
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
        chapter_counter = 1
        for chapter in chapters:
            if chapter.section_type == "chapter" or chapter.section_type not in ["color_illustrations", "extra", "cover", "title_page", "afterword"]:
                chapter.index = chapter_counter
                chapter_counter += 1
            else:
                chapter.index = None
    
    def _get_image_src(self, img_tag) -> Optional[str]:
        if img_tag.name == 'img':
            return img_tag.get('src')
        elif img_tag.name == 'image':
            return img_tag.get('xlink:href') or img_tag.get('href')
        return None
    
    def _is_valid_illustration(self, img_tag, src: str) -> bool:
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
        width = img_tag.get('width')
        height = img_tag.get('height')
        if width and height:
            try:
                illustration.width = int(width)
                illustration.height = int(height)
            except:
                pass
    
    def _are_duplicates(self, illus1: Illustration, illus2: Illustration) -> bool:
        if illus1.filename == illus2.filename:
            return True
        if illus1.content and illus2.content:
            hash1 = hashlib.md5(illus1.content).hexdigest()
            hash2 = hashlib.md5(illus2.content).hexdigest()
            return hash1 == hash2
        return False
    
    def _deduplicate_illustrations(self, illustrations: List[Illustration]) -> List[Illustration]:
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
                    self._log(f"Could not extract {illus.filename}: {str(e)}")
    
    def _chapter_to_dict(self, chapter: Chapter) -> Dict:
        return {
            "index": chapter.index,
            "title": chapter.title,
            "filename": chapter.filename,
            "is_section": chapter.is_section,
            "section_type": chapter.section_type,
            "illustration_count": len(chapter.illustrations)
        }
    
    def _illustration_to_dict(self, illus: Illustration) -> Dict:
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
        self._log(f"✓ JSON exported to {json_path}")
    
    def export_csv(self, result: Dict, output_dir: Path) -> None:
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
        self._log(f"✓ CSV exported to {csv_path}")
    
    def get_results_text(self, result: Dict) -> str:
        lines = []
        title = result.get("title", "Unknown Title")
        
        lines.append("═" * 60)
        lines.append(f" {title}")
        lines.append("═" * 60)
        
        chapters_with_illustrations = []
        for chapter in result["chapters"]:
            if chapter["illustration_count"] > 0:
                chapters_with_illustrations.append(chapter)
        
        if not chapters_with_illustrations:
            chapters_with_illustrations = result["chapters"]
        
        for chapter in chapters_with_illustrations:
            if chapter["illustration_count"] > 0:
                prefix = "✓" if not chapter["is_section"] else "◆"
                lines.append(f"\n{prefix} {chapter['title']}")
                for illus in result["illustrations"]:
                    if illus["chapter"] == chapter["index"] or (illus["section"] == chapter["title"] and chapter["is_section"]):
                        if not illus["is_duplicate"] or self.keep_duplicates:
                            dup_marker = " (duplicate)" if illus["is_duplicate"] else ""
                            lines.append(f"    Illustration #{illus['index']}{dup_marker}")
                            lines.append(f"    {illus['filename']}")
                            if illus.get('width') and illus.get('height'):
                                lines.append(f"    {illus['width']}×{illus['height']}")
            else:
                lines.append(f"\n✓ {chapter['title']}")
                lines.append(f"    (No illustrations)")
        
        lines.append("\n" + "─" * 60)
        unique_count = len([i for i in result['illustrations'] if not i.get('is_duplicate', False)])
        lines.append(f"Total Illustrations: {result['illustration_count']}")
        if not self.keep_duplicates:
            lines.append(f"Unique Illustrations: {unique_count}")
        
        return "\n".join(lines)


class ModernEPUBScannerGUI:
    """Modern Tkinter GUI for the EPUB Scanner with custom styling and validation."""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("EPUB Illustration Scanner")
        self.root.geometry("1000x800")
        self.root.minsize(800, 600)
        
        # Configure style
        self._setup_colors()
        self._setup_styles()
        
        # Variables
        self.scanner = None
        self.scan_thread = None
        self.is_scanning = False
        
        # Validation state
        self.validation_errors = []
        
        # Create UI
        self._create_widgets()
        
        # Center window on screen
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f'{width}x{height}+{x}+{y}')
        
    def _setup_colors(self):
        """Setup color scheme."""
        self.colors = {
            'bg': '#f8f9fa',
            'bg_light': '#ffffff',
            'fg': '#1a1a2e',
            'fg_secondary': '#4a4a6a',
            'accent': '#6c5ce7',
            'accent_light': '#a29bfe',
            'success': '#00b894',
            'error': '#ff6b6b',
            'warning': '#fdcb6e',
            'info': '#74b9ff',
            'border': '#dfe6e9',
            'required': '#ff6b6b',
            'valid': '#00b894'
        }
        
    def _setup_styles(self):
        """Setup ttk styles."""
        style = ttk.Style()
        style.theme_use('clam')
        
        # Configure default styles
        style.configure('.', font=('Segoe UI', 10), background=self.colors['bg'])
        
        # Title style
        style.configure('Title.TLabel', font=('Segoe UI', 18, 'bold'), foreground=self.colors['fg'])
        
        # Header style
        style.configure('Header.TLabel', font=('Segoe UI', 11, 'bold'), foreground=self.colors['fg'])
        
        # Required field label
        style.configure('Required.TLabel', font=('Segoe UI', 10), foreground=self.colors['required'])
        
        # Accent button
        style.configure('Accent.TButton', font=('Segoe UI', 10, 'bold'), 
                       foreground='white', background=self.colors['accent'],
                       padding=(20, 8))
        style.map('Accent.TButton',
                 background=[('active', self.colors['accent_light'])])
        
        # Success button
        style.configure('Success.TButton', font=('Segoe UI', 10, 'bold'),
                       foreground='white', background=self.colors['success'],
                       padding=(20, 8))
        style.map('Success.TButton',
                 background=[('active', '#00a381')])
        
        # Danger button
        style.configure('Danger.TButton', font=('Segoe UI', 10, 'bold'),
                       foreground='white', background=self.colors['error'],
                       padding=(20, 8))
        style.map('Danger.TButton',
                 background=[('active', '#ff5252')])
        
        # Card frame
        style.configure('Card.TFrame', background=self.colors['bg_light'], relief='flat')
        
        # LabelFrame
        style.configure('Card.TLabelframe', background=self.colors['bg_light'], 
                       relief='flat', borderwidth=1)
        style.configure('Card.TLabelframe.Label', font=('Segoe UI', 11, 'bold'),
                       foreground=self.colors['fg'])
        
        # Progressbar
        style.configure('TProgressbar', background=self.colors['accent'], 
                       troughcolor=self.colors['border'], thickness=8)
        
    def _create_widgets(self):
        """Create the GUI widgets."""
        # Main container
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(5, weight=1)
        
        # Header
        header_frame = ttk.Frame(main_frame)
        header_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 20))
        header_frame.columnconfigure(0, weight=1)
        
        title_label = ttk.Label(
            header_frame, 
            text="📚 EPUB Illustration Scanner", 
            style='Title.TLabel'
        )
        title_label.grid(row=0, column=0, sticky=tk.W)
        
        subtitle_label = ttk.Label(
            header_frame,
            text="Extract and catalog illustrations from EPUB files",
            font=('Segoe UI', 10),
            foreground=self.colors['fg_secondary']
        )
        subtitle_label.grid(row=1, column=0, sticky=tk.W, pady=(2, 0))
        
        # Input section
        input_frame = ttk.Frame(main_frame, style='Card.TFrame')
        input_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        input_frame.columnconfigure(1, weight=1)
        
        # File selection
        file_card = ttk.LabelFrame(input_frame, text="📁 Input", style='Card.TLabelframe', padding="15")
        file_card.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        file_card.columnconfigure(1, weight=1)
        
        # Required field indicator
        ttk.Label(file_card, text="* Required", style='Required.TLabel').grid(
            row=0, column=3, sticky=tk.E, padx=(10, 0)
        )
        
        ttk.Label(file_card, text="Path:", style='Header.TLabel').grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        
        self.path_var = tk.StringVar()
        self.path_var.trace('w', lambda *args: self._validate_field('path'))
        path_entry = ttk.Entry(file_card, textvariable=self.path_var, font=('Segoe UI', 10))
        path_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(0, 10))
        
        browse_btn = ttk.Button(
            file_card, 
            text="📂 Browse", 
            command=self._browse_file,
            style='Accent.TButton'
        )
        browse_btn.grid(row=0, column=2)
        
        # Path validation status
        self.path_status = ttk.Label(file_card, text="", font=('Segoe UI', 9))
        self.path_status.grid(row=1, column=1, sticky=tk.W, padx=(0, 10), pady=(5, 0))
        
        # Selection type
        type_frame = ttk.Frame(file_card)
        type_frame.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(10, 0))
        
        ttk.Label(type_frame, text="Selection:", style='Header.TLabel').pack(side=tk.LEFT, padx=(0, 10))
        
        self.file_type_var = tk.StringVar(value="file")
        self.file_type_var.trace('w', lambda *args: self._validate_field('path'))
        ttk.Radiobutton(
            type_frame, 
            text="📄 Single EPUB", 
            variable=self.file_type_var, 
            value="file"
        ).pack(side=tk.LEFT, padx=(0, 15))
        ttk.Radiobutton(
            type_frame, 
            text="📁 Folder of EPUBs", 
            variable=self.file_type_var, 
            value="folder"
        ).pack(side=tk.LEFT)
        
        # Output path
        output_frame = ttk.Frame(file_card)
        output_frame.grid(row=3, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=(10, 0))
        output_frame.columnconfigure(1, weight=1)
        
        ttk.Label(output_frame, text="Output:", style='Header.TLabel').grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        
        self.output_var = tk.StringVar(value=str(Path("output").absolute()))
        self.output_var.trace('w', lambda *args: self._validate_field('output'))
        output_entry = ttk.Entry(output_frame, textvariable=self.output_var, font=('Segoe UI', 10))
        output_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(0, 10))
        
        output_browse_btn = ttk.Button(
            output_frame, 
            text="📂 Browse", 
            command=self._browse_output,
            style='Accent.TButton'
        )
        output_browse_btn.grid(row=0, column=2)
        
        # Output validation status
        self.output_status = ttk.Label(output_frame, text="", font=('Segoe UI', 9))
        self.output_status.grid(row=1, column=1, sticky=tk.W, padx=(0, 10), pady=(5, 0))
        
        # Options section
        options_card = ttk.LabelFrame(input_frame, text="⚙️ Options", style='Card.TLabelframe', padding="15")
        options_card.grid(row=1, column=0, sticky=(tk.W, tk.E))
        options_card.columnconfigure(0, weight=1)
        
        # Options grid - two columns for better layout
        left_options = ttk.Frame(options_card)
        left_options.grid(row=0, column=0, sticky=(tk.W, tk.N))
        
        right_options = ttk.Frame(options_card)
        right_options.grid(row=0, column=1, sticky=(tk.W, tk.N), padx=(20, 0))
        
        self.keep_duplicates_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            left_options, 
            text="🔄 Keep duplicate images", 
            variable=self.keep_duplicates_var
        ).pack(anchor=tk.W, pady=2)
        
        self.extract_images_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            left_options, 
            text="💾 Extract illustrations", 
            variable=self.extract_images_var
        ).pack(anchor=tk.W, pady=2)
        
        self.generate_pdf_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            left_options, 
            text="📄 Generate PDF catalog", 
            variable=self.generate_pdf_var
        ).pack(anchor=tk.W, pady=2)
        
        # PDF style
        pdf_style_frame = ttk.Frame(right_options)
        pdf_style_frame.pack(anchor=tk.W, pady=2)
        
        ttk.Label(pdf_style_frame, text="PDF Style:", style='Header.TLabel').pack(side=tk.LEFT, padx=(0, 10))
        
        self.pdf_style_var = tk.StringVar(value="detailed")
        ttk.Radiobutton(
            pdf_style_frame, 
            text="Detailed", 
            variable=self.pdf_style_var, 
            value="detailed"
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(
            pdf_style_frame, 
            text="Simple", 
            variable=self.pdf_style_var, 
            value="simple"
        ).pack(side=tk.LEFT)
        
        # Button row
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(15, 10))
        
        self.scan_btn = ttk.Button(
            button_frame, 
            text="🚀 Start Scan", 
            command=self._start_scan,
            style='Success.TButton'
        )
        self.scan_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.stop_btn = ttk.Button(
            button_frame, 
            text="⏹ Stop", 
            command=self._stop_scan, 
            state=tk.DISABLED,
            style='Danger.TButton'
        )
        self.stop_btn.pack(side=tk.LEFT)
        
        # Progress section
        progress_frame = ttk.Frame(main_frame)
        progress_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        progress_frame.columnconfigure(0, weight=1)
        
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            progress_frame, 
            variable=self.progress_var, 
            maximum=100
        )
        self.progress_bar.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 10))
        
        self.progress_label = ttk.Label(progress_frame, text="0%", font=('Segoe UI', 10, 'bold'))
        self.progress_label.grid(row=0, column=1)
        
        # Status
        self.status_label = ttk.Label(
            main_frame, 
            text="🟢 Ready", 
            font=('Segoe UI', 10)
        )
        self.status_label.grid(row=4, column=0, sticky=tk.W, pady=(0, 10))
        
        # Results
        results_card = ttk.LabelFrame(main_frame, text="📊 Results", style='Card.TLabelframe', padding="10")
        results_card.grid(row=5, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        results_card.columnconfigure(0, weight=1)
        results_card.rowconfigure(0, weight=1)
        
        # Create text widget with custom styling
        text_frame = ttk.Frame(results_card)
        text_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        
        self.results_text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg=self.colors['bg_light'],
            fg=self.colors['fg'],
            insertbackground=self.colors['fg'],
            relief=tk.FLAT,
            borderwidth=0,
            padx=10,
            pady=10,
            spacing1=2,
            spacing2=1
        )
        self.results_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.results_text.yview)
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.results_text.config(yscrollcommand=scrollbar.set)
        
        # Configure grid weights
        main_frame.rowconfigure(5, weight=1)
        
        # Initialize validation
        self._validate_all()
        
    def _validate_field(self, field):
        """Validate a specific field."""
        if field == 'path':
            self._validate_path()
        elif field == 'output':
            self._validate_output()
        self._update_scan_button()
    
    def _validate_all(self):
        """Validate all fields."""
        self._validate_path()
        self._validate_output()
        self._update_scan_button()
    
    def _validate_path(self) -> bool:
        """Validate the input path."""
        path_str = self.path_var.get().strip()
        is_valid = False
        message = ""
        color = self.colors['error']
        
        if not path_str:
            message = "⚠️ Required field - please select an EPUB file or folder"
            color = self.colors['error']
        else:
            path = Path(path_str)
            if not path.exists():
                message = "❌ Path does not exist"
                color = self.colors['error']
            elif path.is_file() and path.suffix.lower() != '.epub':
                message = "❌ Selected file is not an EPUB"
                color = self.colors['error']
            elif path.is_dir():
                # Check if folder contains EPUBs
                epub_files = list(path.rglob('*.epub'))
                if epub_files:
                    message = f"✅ Found {len(epub_files)} EPUB file(s)"
                    color = self.colors['valid']
                    is_valid = True
                else:
                    message = "⚠️ No EPUB files found in this folder"
                    color = self.colors['warning']
            else:
                # Single EPUB file
                message = f"✅ Valid EPUB file: {path.name}"
                color = self.colors['valid']
                is_valid = True
        
        self.path_status.config(text=message, foreground=color)
        return is_valid
    
    def _validate_output(self) -> bool:
        """Validate the output directory."""
        output_str = self.output_var.get().strip()
        is_valid = False
        message = ""
        color = self.colors['error']
        
        if not output_str:
            message = "⚠️ Required field - please select an output directory"
            color = self.colors['error']
        else:
            output_path = Path(output_str)
            try:
                # Check if we can create the directory
                output_path.mkdir(parents=True, exist_ok=True)
                message = f"✅ Valid output directory: {output_path}"
                color = self.colors['valid']
                is_valid = True
            except Exception as e:
                message = f"❌ Cannot create directory: {str(e)}"
                color = self.colors['error']
        
        self.output_status.config(text=message, foreground=color)
        return is_valid
    
    def _update_scan_button(self):
        """Update the scan button state based on validation."""
        path_valid = self._validate_path()
        output_valid = self._validate_output()
        self.scan_btn.config(state=tk.NORMAL if (path_valid and output_valid and not self.is_scanning) else tk.DISABLED)
        
        if not self.is_scanning:
            if path_valid and output_valid:
                self.scan_btn.config(text="🚀 Start Scan", style='Success.TButton')
            else:
                self.scan_btn.config(text="⚠️ Fix Validation Errors", style='Accent.TButton')
    
    def _browse_file(self):
        """Browse for EPUB file or folder."""
        if self.file_type_var.get() == "file":
            filepath = filedialog.askopenfilename(
                title="Select EPUB file",
                filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")]
            )
            if filepath:
                self.path_var.set(filepath)
        else:
            folder = filedialog.askdirectory(
                title="Select folder containing EPUB files"
            )
            if folder:
                self.path_var.set(folder)
    
    def _browse_output(self):
        """Browse for output directory."""
        folder = filedialog.askdirectory(
            title="Select output directory"
        )
        if folder:
            self.output_var.set(folder)
    
    def _log_message(self, message):
        """Add a message to the log with color coding."""
        tags = []
        if "✓" in message or "✅" in message or "Complete" in message:
            tags.append("success")
        elif "✗" in message or "❌" in message or "Error" in message or "error" in message.lower():
            tags.append("error")
        elif "⚠" in message or "Warning" in message:
            tags.append("warning")
        elif "📊" in message or "===" in message:
            tags.append("header")
        elif "📚" in message or "🚀" in message:
            tags.append("info")
        
        # Configure tags
        self.results_text.tag_config("success", foreground=self.colors['success'])
        self.results_text.tag_config("error", foreground=self.colors['error'])
        self.results_text.tag_config("warning", foreground=self.colors['warning'])
        self.results_text.tag_config("info", foreground=self.colors['info'])
        self.results_text.tag_config("header", foreground=self.colors['accent'], font=('Consolas', 10, 'bold'))
        
        # Insert with appropriate tag
        self.results_text.insert(tk.END, message + "\n", tuple(tags) if tags else ())
        self.results_text.see(tk.END)
        self.root.update_idletasks()
    
    def _update_progress(self, message, value=None):
        """Update progress bar and status."""
        if value is not None:
            self.progress_var.set(value)
            self.progress_label.config(text=f"{int(value)}%")
        
        if message:
            # Update status with dynamic styling
            if "Processing" in message:
                status_text = f"🔄 {message}"
                color = self.colors['info']
            elif "Complete" in message or "✓" in message or "✅" in message:
                status_text = f"✅ {message}"
                color = self.colors['success']
            elif "Error" in message or "✗" in message or "❌" in message:
                status_text = f"❌ {message}"
                color = self.colors['error']
            else:
                status_text = f"ℹ️ {message}"
                color = self.colors['fg']
            
            self.status_label.config(text=status_text, foreground=color)
            self._log_message(message)
        
        self.root.update_idletasks()
    
    def _start_scan(self):
        """Start the scanning process."""
        if self.is_scanning:
            return
        
        # Validate before starting
        if not self._validate_all():
            messagebox.showwarning("Validation Error", 
                "Please fix all validation errors before starting the scan.\n"
                "Make sure both input and output paths are valid.")
            return
        
        path_str = self.path_var.get().strip()
        path = Path(path_str)
        
        # Determine files to scan
        if path.is_file() and path.suffix.lower() == '.epub':
            files_to_scan = [path]
        elif path.is_dir():
            files_to_scan = list(path.rglob('*.epub'))
            if not files_to_scan:
                messagebox.showerror("Error", "No EPUB files found in the selected folder.")
                return
        else:
            messagebox.showerror("Error", "Invalid input path.")
            return
        
        # Set output directory
        output_dir = Path(self.output_var.get().strip())
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Error", f"Cannot create output directory: {str(e)}")
            return
        
        # Disable controls
        self.scan_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.is_scanning = True
        self.progress_var.set(0)
        self.progress_label.config(text="0%")
        self.results_text.delete(1.0, tk.END)
        self._log_message("🚀 Starting scan...")
        self._log_message(f"📁 Input: {path}")
        self._log_message(f"📂 Output: {output_dir}")
        self._log_message(f"📚 Found {len(files_to_scan)} EPUB file(s)")
        self._log_message("=" * 60)
        
        # Create scanner with callbacks
        self.scanner = EPUBScanner(
            log_callback=self._log_message,
            progress_callback=self._update_progress
        )
        
        # Set options
        self.scanner.keep_duplicates = self.keep_duplicates_var.get()
        self.scanner.extract_images = self.extract_images_var.get()
        self.scanner.generate_pdf = self.generate_pdf_var.get()
        self.scanner.pdf_style = self.pdf_style_var.get()
        self.scanner.output_dir = output_dir
        
        # Start scan in separate thread
        self.scan_thread = threading.Thread(target=self._scan_worker, args=(files_to_scan,))
        self.scan_thread.daemon = True
        self.scan_thread.start()
    
    def _scan_worker(self, files_to_scan):
        """Worker thread for scanning multiple files."""
        total_files = len(files_to_scan)
        successful = 0
        total_illustrations = 0
        
        try:
            for idx, filepath in enumerate(files_to_scan, 1):
                if self.scanner.should_stop:
                    self._log_message("⏹ Scan cancelled by user.")
                    break
                
                # Update progress
                progress = (idx / total_files) * 100
                self._update_progress(f"Processing {filepath.name} ({idx}/{total_files})...", progress)
                
                result = self.scanner.process_epub(filepath)
                
                if result:
                    self.scanner.scanned_books.append(result)
                    self.scanner.total_illustrations += result['illustration_count']
                    total_illustrations += result['illustration_count']
                    
                    # Export results
                    output_dir = self.scanner.output_dir / "data"
                    self.scanner.export_json(result, output_dir)
                    self.scanner.export_csv(result, output_dir)
                    
                    # Display results
                    results_text = self.scanner.get_results_text(result)
                    self._log_message("\n" + results_text)
                    self._log_message(f"\n✅ Export complete. Files saved to {output_dir}")
                    
                    if self.scanner.extract_images:
                        self._log_message(f"💾 Images extracted to {self.scanner.output_dir}/extracted_images/")
                    if self.scanner.generate_pdf and PDF_SUPPORT:
                        self._log_message(f"📄 PDF catalogs generated in {self.scanner.output_dir}/pdf_catalogs/")
                    
                    successful += 1
                else:
                    self._log_message(f"❌ Failed to process {filepath.name}")
            
            # Final summary
            self._log_message("\n" + "=" * 60)
            self._log_message("📊 SCAN COMPLETE")
            self._log_message(f"✅ Successfully processed: {successful}/{total_files}")
            self._log_message(f"📚 Total illustrations found: {total_illustrations}")
            
            if self.scanner.generate_pdf and PDF_SUPPORT:
                self._log_message(f"📄 PDF catalogs: {self.scanner.output_dir}/pdf_catalogs/")
            if self.scanner.extract_images:
                self._log_message(f"💾 Extracted images: {self.scanner.output_dir}/extracted_images/")
            
            self._log_message("=" * 60)
            self._log_message("✨ All tasks completed successfully!")
            
        except Exception as e:
            self._log_message(f"❌ Fatal error: {str(e)}")
            import traceback
            self._log_message(traceback.format_exc())
        
        finally:
            self.root.after(0, self._scan_complete)
    
    def _scan_complete(self):
        """Called when scan is complete."""
        self.is_scanning = False
        self.stop_btn.config(state=tk.DISABLED)
        self.status_label.config(text="✅ Scan Complete", foreground=self.colors['success'])
        self.progress_var.set(100)
        self.progress_label.config(text="100%")
        self._update_scan_button()
    
    def _stop_scan(self):
        """Stop the scanning process."""
        if self.scanner:
            self.scanner.stop()
            self._log_message("⏹ Stopping scan...")
            self.stop_btn.config(state=tk.DISABLED)
    
    def run(self):
        """Run the GUI."""
        self.root.mainloop()


def main():
    """Main entry point for the application."""
    app = ModernEPUBScannerGUI()
    app.run()


if __name__ == "__main__":
    main()