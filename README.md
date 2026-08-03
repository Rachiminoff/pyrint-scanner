# pyrint-scanner

A production-ready Python utility for scanning EPUB files and automatically determining which chapter each illustration belongs to.

Built for light novels, ebooks, and other illustrated EPUBs, **pyrint-scanner** analyzes an EPUB's internal structure, detects chapter boundaries, and maps every illustration to its corresponding chapter. It can also extract illustrations and export the results in multiple machine-readable formats.

---

## ✨ Features

### 📖 Smart Chapter Detection
Detects chapter titles using the EPUB table of contents, navigation files, and HTML content.

### 🖼️ Illustration Mapping
Automatically associates every illustration with the chapter it belongs to.

### 📦 Multiple Export Formats
Export scan results as **JSON** or **CSV** for further processing.

### 📸 Optional Image Extraction
Extract illustrations directly from the EPUB archive while preserving their original quality.

### 📄 PDF Catalog Generation
Generate clean PDF catalogs containing thumbnails, chapter information, and image metadata.

### 🖥️ Modern GUI
Easy-to-use graphical interface with real-time validation, progress tracking, and configurable options.

### 📚 Batch Processing
Scan a single EPUB or process an entire directory in one operation.

### 🔍 Duplicate Detection
Detects duplicate illustrations using MD5 hashing to prevent redundant exports.

### ⚡ Fast & Lightweight
Optimized for speed with a minimal dependency footprint.

---

## 🚀 Quick Start

### Clone the repository

```bash
git clone https://github.com/Rachiminoff/pyrint-scanner.git
cd pyrint-scanner
```

### Install dependencies

```bash
pip install -r requirements.txt
```

### Launch the application

```bash
python src/pyrint-scanner.py
```

---

## 📋 Requirements

- Python **3.8+**
- ebooklib
- beautifulsoup4
- reportlab *(for PDF generation)*
- tkinter *(included with most Python installations)*

---

## 💡 Use Cases

- Organize illustrations by chapter
- Build illustration galleries
- Analyze EPUB structures
- Prepare datasets for ebook tooling
- Automate light novel processing pipelines
- Generate visual catalogs of book illustrations

---

## 📄 License

This project is licensed under the **MIT License**.
