#!/usr/bin/env python3
"""
epub_scanner.py - A production-quality EPUB illustration scanner and analyzer.

This tool scans EPUB files, detects illustrations, maps them to chapters/sections,
and exports results in JSON/CSV formats with optional image extraction.
"""

import csv
import json
import os
import re
import sys
import time
import zipfile
from dataclasses import dataclass, field
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

# Constants
DEFAULT_OUTPUT_DIR = Path("output")
EXTRACT_IMAGES_DIR = Path("extracted_images")
ICON_SIZE_THRESHOLD = (50, 50)  # Images smaller than this are likely icons
MIN_IMAGE_SIZE = (100, 100)  # Minimum size for actual illustrations
SVG_NAMESPACE = "http://www.w3.org/2000/svg"

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


class EPUBScanner:
    """Main scanner class for processing EPUB files."""
    
    def __init__(self, console: Console):
        self.console = console
        self.scanned_books: List[Dict] = []
        self.total_time = 0.0
        self.total_illustrations = 0
        self.keep_duplicates = False
        self.extract_images = False
        self.output_dir = DEFAULT_OUTPUT_DIR
        self.toc_titles: Dict[str, str] = {}  # Store TOC titles by filename
        
    def process_epub(self, filepath: Path, progress_callback=None) -> Optional[Dict]:
        """Process a single EPUB file and return its data."""
        try:
            # Update status using callback instead of console.status()
            if progress_callback:
                progress_callback(f"Processing {filepath.name}...")
            
            # Load the EPUB
            book = epub.read_epub(str(filepath))
            
            # Extract metadata
            title = self._get_title(book)
            
            # Build TOC mapping
            self.toc_titles = self._build_toc_mapping(book)
            
            # Process in reading order
            chapters, illustrations = self._process_reading_order(book)
            
            # Deduplicate illustrations if needed
            if not self.keep_duplicates:
                illustrations = self._deduplicate_illustrations(illustrations)
            
            # Sort and renumber illustrations by reading order
            illustrations.sort(key=lambda x: x.index)
            for idx, illus in enumerate(illustrations, 1):
                illus.index = idx
            
            # Assign chapter numbers
            self._assign_chapter_numbers(chapters)
            
            # Build result
            result = {
                "filepath": str(filepath),
                "title": title,
                "chapters": [self._chapter_to_dict(c) for c in chapters],
                "illustrations": [self._illustration_to_dict(i) for i in illustrations],
                "illustration_count": len(illustrations)
            }
            
            # Extract images if requested
            if self.extract_images:
                self._extract_illustrations(filepath.stem, illustrations)
            
            return result
            
        except Exception as e:
            self.console.print(f"[red]✗ Error processing {filepath.name}: {str(e)}[/]")
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
            # Try to get TOC from ebooklib
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_NAVIGATION:
                    content = item.get_content().decode('utf-8', errors='ignore')
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    # Find all navigation links
                    for link in soup.find_all(['a', 'link']):
                        href = link.get('href')
                        text = link.get_text(strip=True)
                        if href and text:
                            # Clean up href
                            href = href.split('#')[0]  # Remove fragment
                            if href:
                                toc_map[href] = text
        except:
            pass
        
        # Also try to get TOC from the spine
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
        
        # Get spine items (reading order)
        spine_item_ids = []
        for spine_item in book.spine:
            if isinstance(spine_item, tuple):
                spine_item_ids.append(spine_item[0])
            elif isinstance(spine_item, str):
                spine_item_ids.append(spine_item)
        
        # Get all document items
        doc_items = {}
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                doc_items[item.get_id()] = item
        
        # Process each document in spine order
        for item_id in spine_item_ids:
            if item_id in doc_items:
                item = doc_items[item_id]
                
                # Parse content
                try:
                    content = item.get_content().decode('utf-8', errors='ignore')
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    # Get TOC title if available
                    toc_title = self.toc_titles.get(item.file_name, '')
                    
                    # Detect section
                    section_title = self._detect_section_title(soup, item.file_name, toc_title)
                    section_type = self._detect_section_type(soup, item.file_name, section_title)
                    
                    # Create chapter
                    chapter = Chapter(
                        index=len(chapters),
                        title=section_title or f"Section {len(chapters) + 1}",
                        filename=item.file_name,
                        content=content,
                        is_section=section_type != "chapter",
                        section_type=section_type
                    )
                    
                    # Find illustrations in this document
                    img_tags = soup.find_all(['img', 'image'])
                    for img in img_tags:
                        src = self._get_image_src(img)
                        if src and self._is_valid_illustration(img, src):
                            image_counter += 1
                            illustration = Illustration(
                                index=image_counter,
                                section=section_title or section_type.capitalize(),
                                chapter=len(chapters) if section_type not in ["color_illustrations", "cover", "title_page"] else None,
                                filename=os.path.basename(src),
                                filepath=src,
                                content=self._get_image_content(book, src)
                            )
                            
                            # Try to get dimensions
                            self._get_image_dimensions(img, illustration)
                            
                            # Check if it's a duplicate
                            if not self.keep_duplicates:
                                for existing in illustrations:
                                    if self._are_duplicates(existing, illustration):
                                        illustration.is_duplicate = True
                                        break
                            
                            # Add to chapter and illustrations list
                            if not illustration.is_duplicate or self.keep_duplicates:
                                chapter.illustrations.append(illustration)
                                illustrations.append(illustration)
                    
                    chapters.append(chapter)
                    
                except Exception as e:
                    self.console.print(f"[yellow]Warning: Could not parse {item.file_name}: {str(e)}[/]")
                    continue
        
        # Also process any documents not in spine (sometimes happens)
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT and item.get_id() not in spine_item_ids:
                # Skip navigation documents
                if item.file_name and ('nav' in item.file_name.lower() or 'toc' in item.file_name.lower()):
                    continue
                
                try:
                    content = item.get_content().decode('utf-8', errors='ignore')
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    # Only process if it has meaningful content
                    text = soup.get_text(strip=True)
                    if len(text) > 100:  # Has substantial text
                        toc_title = self.toc_titles.get(item.file_name, '')
                        section_title = self._detect_section_title(soup, item.file_name, toc_title)
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
    
    def _detect_section_title(self, soup: BeautifulSoup, filename: str, toc_title: str = "") -> Optional[str]:
        """Detect section title from document with improved heuristics for light novels."""
        # First, check if we have a TOC title
        if toc_title:
            return toc_title
        
        # Try to find the main heading - look for centered text that might be chapter titles
        heading_text = None
        
        # Look for headings with specific classes
        for tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            headings = soup.find_all(tag)
            for heading in headings:
                text = heading.get_text(strip=True)
                if text and len(text) < 200:
                    # Check if it looks like a chapter title
                    if re.search(r'chapter|prologue|epilogue|afterword|appendix|interlude|side|story|bonus', text, re.IGNORECASE):
                        heading_text = text
                        break
                    if not heading_text and len(text) > 3:
                        heading_text = text
            if heading_text:
                break
        
        # Look for centered paragraphs (common in light novels)
        if not heading_text:
            for p in soup.find_all('p', style=re.compile(r'text-align:\s*center', re.I)):
                text = p.get_text(strip=True)
                if text and 3 < len(text) < 100:
                    # Check for chapter-like patterns
                    if (re.search(r'^第', text) or  # Japanese chapter marker
                        re.search(r'Chapter|Prologue|Epilogue|Afterword|Interlude', text, re.IGNORECASE) or
                        re.search(r'^\d+\.?\s*[—\-]\s*', text) or  # Numbered chapter
                        re.search(r'[A-Z][a-z]+\s+[A-Z][a-z]+', text)):  # Two capitalized words
                        heading_text = text
                        break
                    # If it's a short centered text, likely a title
                    if len(text) < 50 and not heading_text:
                        heading_text = text
        
        # Look for decorative dividers with text (☆☆☆ pattern in light novels)
        if not heading_text:
            # Find text that appears after decorative dividers
            for div in soup.find_all(['div', 'p']):
                text = div.get_text(strip=True)
                if text and 3 < len(text) < 100:
                    # Check if it follows a decorative pattern
                    if '☆☆☆' in str(div.parent) or '***' in str(div.parent) or '———' in str(div.parent):
                        heading_text = text
                        break
        
        # Try the title tag
        if not heading_text:
            title = soup.find('title')
            if title:
                text = title.get_text(strip=True)
                if text:
                    text = re.sub(r'\s*[–—-]\s*.*$', '', text)
                    text = re.sub(r'^(Chapter|Ch\.?|Section|Sec\.?)\s*\d+\s*[:.-]\s*', '', text, flags=re.I)
                    if text and len(text) < 100:
                        heading_text = text
        
        # Look for any paragraph with chapter-like keywords
        if not heading_text:
            for p in soup.find_all('p'):
                text = p.get_text(strip=True)
                if text and 3 < len(text) < 100:
                    # Check for chapter keywords
                    if re.search(r'^chapter|^prologue|^epilogue|^afterword|^appendix|^interlude|^side\s+story', text, re.IGNORECASE):
                        heading_text = text
                        break
                    # Check for numbered chapter (e.g., "Chapter 1", "Chapter One")
                    if re.search(r'chapter\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)', text, re.IGNORECASE):
                        heading_text = text
                        break
        
        # Try the filename
        if not heading_text:
            name = os.path.splitext(os.path.basename(filename))[0]
            name = name.replace('_', ' ').replace('-', ' ')
            name = re.sub(r'^(ch|chapter|chap|section|sec)\s*\d+\s*[-:]\s*', '', name, flags=re.I)
            name = re.sub(r'^part\d+', '', name, flags=re.I)
            if name and len(name) > 2 and not name.startswith('part'):
                heading_text = name.strip()
        
        return heading_text
    
    def _detect_section_type(self, soup: BeautifulSoup, filename: str, title: Optional[str] = None) -> str:
        """Detect the type of section with improved detection."""
        text = soup.get_text().lower()
        
        # Check if it's a cover or title page
        if re.search(r'cover|title page|half title', filename, re.IGNORECASE):
            return "cover"
        
        # Check for color illustrations section
        if re.search(r'color|colored?|colorplate', filename, re.IGNORECASE) or \
           re.search(r'color', text, re.IGNORECASE) and re.search(r'illustration|plate', text, re.IGNORECASE):
            return "color_illustrations"
        
        # Check for specific section types
        for section_type, patterns in SECTION_PATTERNS.items():
            for pattern in patterns:
                if title and re.search(pattern, title, re.IGNORECASE):
                    return section_type
                if re.search(pattern, filename, re.IGNORECASE):
                    return section_type
                if re.search(pattern, text, re.IGNORECASE):
                    return section_type
        
        # Check for chapter pattern
        chapter_match = re.search(r'chapter\s*(\d+)', text, re.IGNORECASE) or \
                       re.search(r'chapter\s*(\d+)', filename, re.IGNORECASE) or \
                       (title and re.search(r'chapter\s*(\d+)', title, re.IGNORECASE))
        if chapter_match:
            return "chapter"
        
        # Check for numbered section
        if re.search(r'^\s*(?:\d+\.?\s*|第\d+[章话])\s*', text[:200]):
            return "chapter"
        
        # Check if it's a short story or extra
        if re.search(r'short story|side story|bonus|extra', text, re.IGNORECASE):
            return "extra"
        
        # Check for afterword
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
        
        # Skip common icon patterns
        icon_patterns = ['icon', 'logo', 'banner', 'spacer', 'dot', 'bullet', 'separator', 'btn', 'button']
        if any(pattern in src.lower() for pattern in icon_patterns):
            return False
        
        # Skip common image extensions that are likely not illustrations
        skip_extensions = ['.gif']
        if any(src.lower().endswith(ext) for ext in skip_extensions):
            return False
        
        # Check dimensions if available
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
            import hashlib
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
                import hashlib
                key = hashlib.md5(illus.content).hexdigest()
            if key not in seen:
                seen.add(key)
                unique.append(illus)
            else:
                illus.is_duplicate = True
        return unique
    
    def _extract_illustrations(self, book_title: str, illustrations: List[Illustration]) -> None:
        """Extract illustrations to the output directory."""
        output_path = self.output_dir / book_title
        output_path.mkdir(parents=True, exist_ok=True)
        
        for illus in illustrations:
            if illus.content:
                try:
                    ext = os.path.splitext(illus.filename)[1]
                    if not ext:
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
            "filename": illus.filename,
            "filepath": illus.filepath,
            "width": illus.width,
            "height": illus.height,
            "is_duplicate": illus.is_duplicate
        }
    
    def export_json(self, result: Dict, output_dir: Path) -> None:
        """Export results to JSON."""
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(result["filepath"]).stem
        json_path = output_dir / f"{filename}.json"
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        self.console.print(f"[green]✓ JSON exported to {json_path}[/]")
    
    def export_csv(self, result: Dict, output_dir: Path) -> None:
        """Export results to CSV."""
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(result["filepath"]).stem
        csv_path = output_dir / f"{filename}.csv"
        
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Volume', 'Illustration', 'Section', 'Chapter', 'Filename'])
            
            for illus in result["illustrations"]:
                chapter_num = illus["chapter"] if illus["chapter"] is not None else ''
                chapter_title = ''
                for chapter in result["chapters"]:
                    if chapter["index"] == illus["chapter"]:
                        chapter_title = chapter["title"]
                        break
                
                writer.writerow([
                    result["title"],
                    illus["index"],
                    chapter_title or illus["section"],
                    chapter_num,
                    illus["filename"]
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
                            self.console.print(f"    [yellow]Illustration #{illus['index']}[/]")
                            self.console.print(f"    [dim]{illus['filename']}[/]")
            else:
                self.console.print(f"\n[dim]✓ {chapter['title']}[/]")
                self.console.print(f"    [dim](No illustrations)[/]")
        
        self.console.print()
        self.console.print(f"[bold cyan]─[/]" * 50)
        self.console.print(f"[green]Illustrations: {result['illustration_count']}[/]")
        self.console.print()
    
    def display_summary(self) -> None:
        """Display final summary table."""
        if not self.scanned_books:
            self.console.print("[red]No books were scanned successfully.[/]")
            return
        
        table = Table(title="Scan Summary", show_header=True, header_style="bold magenta")
        table.add_column("EPUB", style="cyan", no_wrap=True)
        table.add_column("Images", justify="right", style="green")
        
        total_images = 0
        for book in self.scanned_books:
            title = book.get("title", "Unknown")
            count = book.get("illustration_count", 0)
            table.add_row(title, str(count))
            total_images += count
        
        self.console.print(table)
        self.console.print()
        self.console.print(f"Books scanned: [cyan]{len(self.scanned_books)}[/]")
        self.console.print(f"Illustrations: [green]{total_images}[/]")
        self.console.print(f"Time elapsed : [yellow]{self.total_time:.2f} seconds[/]")


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
    
    if scanner.extract_images:
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