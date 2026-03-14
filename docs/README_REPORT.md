# Project Report — Support Ticket Management System

This folder contains the project report for **Support Ticket Management System with AI-Assisted Reply Suggestion** prepared as per CHRIST (Deemed to be University) Department of Computer Science guidelines (2022).

## Files

| File | Purpose |
|-----|--------|
| **PROJECT_REPORT_FORMATTING.md** | Formatting instructions to apply in Word (font, margins, headers, page numbers, TOC). |
| **PROJECT_REPORT.md** | Full report content: Title page, Certificate, Acknowledgments, Abstract, TOC, List of Tables/Figures, Abbreviations, Chapters 1–6, Appendices, References. |

## Producing the final document in Word

1. **Open in Word:** Open `PROJECT_REPORT.md` in Microsoft Word (File → Open → select the file). Word will interpret the Markdown structure. Alternatively, use [Pandoc](https://pandoc.org/) to convert to DOCX:
   ```bash
   pandoc PROJECT_REPORT.md -o PROJECT_REPORT.docx
   ```

2. **Apply formatting (see PROJECT_REPORT_FORMATTING.md):**
   - Font: Times New Roman, 12 pt (body); Chapter titles 16 pt Bold, CAPITALS, Centered; Side headings 12 pt Bold, CAPITALS.
   - Line spacing: 1.5.
   - Margins: Left 1.5", Right 1", Top 1", Bottom 1".
   - Header (font 10): Project name (left), Page number (right); border line below.
   - Footer (font 10): "Department of Computer Science, CHRIST (Deemed to be University)"; border line above.
   - Page numbers: Roman (iii, iv…) for Acknowledgments; no number for Title/Certificate/TOC; Section Break before Chapter 1; Arabic (1, 2…) from Chapter 1.

3. **Fill in placeholders** in the report:
   - **[Student Name(s)]**, **[Register Number(s)]**, **[Guide Name]**, **Place**, **Date** on Title and Certificate pages.
   - Guide name in Acknowledgments.

4. **Figures and tables:** The report references figures (block diagram, DFD, ER, architecture, screenshots) and tables. Draw diagrams in Word using Insert → Shapes / Drawing canvas as per guidelines. Insert actual screen shots for Section 4.4 and update List of Figures and List of Tables with page numbers.

5. **Table of Contents:** Use References → Table of Contents → Automatic Table of Contents. Update (right-click TOC → Update Field) after any heading or page changes.

6. **Page count:** Ensure the final document is between 60 and 100 pages (expand sections if needed; add more test cases, code snippets, or appendix material).

7. **Binding:** Print on A4, one-sided; use light blue spiral binding as specified.

## Report structure (order of pages)

1. Title Page  
2. Certificate  
3. Acknowledgments (page iii)  
4. Abstract (iv)  
5. Table of Contents (no page number)  
6. List of Tables  
7. List of Figures  
8. Abbreviations (optional)  
9. Chapter 1 – Introduction  
10. Chapter 2 – System Analysis and Requirements  
11. Chapter 3 – System Design  
12. Chapter 4 – Implementation  
13. Chapter 5 – Testing  
14. Chapter 6 – Conclusion  
15. Appendices (A, A.1–A.4, B.1–B.4, C.1–C.2)  
16. References  

All content for the above is in **PROJECT_REPORT.md**. Adjust section numbering and TOC to match your department’s sample TOC if different.
