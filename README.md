# pyrint-scanner

A production-ready Python utility for scanning EPUB files and automatically determining which chapter each illustration belongs to.

Designed for light novels, ebooks, and other illustrated EPUBs, **pyrint-scanner** analyzes an EPUB's structure, detects chapter boundaries, and maps every illustration to its corresponding chapter. It can also optionally extract illustrations and export the results in machine-readable formats.

## Features

- **Smart Chapter Detection**  
  Detects chapter titles using EPUB navigation files and HTML content.

- **Illustration Mapping**  
  Automatically associates each illustration with its corresponding chapter.

- **Multiple Export Formats**  
  Export results as **JSON** or **CSV**.

- **Optional Image Extraction**  
  Extract illustrations directly from the EPUB archive.

- **Rich Command-Line Interface**  
  Clean terminal output with progress bars, tables, and status indicators.

- **Batch Processing**  
  Process a single EPUB or scan an entire directory.

- **Fast & Lightweight**  
  Built for efficient processing with minimal dependencies.

## Quick Start

```bash
# Clone the repository
git clone https://github.com/Rachiminoff/pyrint-scanner.git
cd pyrint-scanner

# Install dependencies
pip install -r requirements.txt

# Run the scanner
python pyrint_scanner.py
```

## Use Cases

- Organize illustrations by chapter
- Build illustration galleries
- Analyze EPUB structures
- Prepare datasets for ebook tooling
- Automate light novel processing pipelines

## License

This project is licensed under the MIT License.