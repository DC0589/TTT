import csv
import io
import re
import zipfile
from xml.etree import ElementTree

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
SKIP_COMPANIES = {"fresher", "own company", "-", "na", "n/a"}


def _column_index(ref):
    letters = re.match(r"[A-Z]+", ref).group()
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch) - 64
    return index - 1


def read_xlsx_rows(file_obj):
    with zipfile.ZipFile(file_obj) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        sheet = ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.iter(f"{{{NS['m']}}}row"):
        values = {}
        for cell in row.findall("m:c", NS):
            v = cell.find("m:v", NS)
            inline = cell.find("m:is", NS)
            if cell.get("t") == "inlineStr" and inline is not None:
                text = "".join(t.text or "" for t in inline.iter(f"{{{NS['m']}}}t"))
            elif v is None or v.text is None:
                continue
            elif cell.get("t") == "s":
                text = shared[int(v.text)]
            else:
                text = v.text
            values[_column_index(cell.get("r"))] = text.strip()
        if values:
            rows.append([values.get(i, "") for i in range(max(values) + 1)])
    return rows


def read_csv_rows(file_obj):
    text = file_obj.read().decode("utf-8-sig")
    return [row for row in csv.reader(io.StringIO(text)) if any(c.strip() for c in row)]


def parse_placements(rows):
    """Return (entries, error). Uses the company column closest to the year column."""
    if not rows:
        return [], "The file is empty."
    headers = [h.strip().lower() for h in rows[0]]
    if "name" not in headers or "company" not in headers:
        return [], "The first row needs the headings: Name, Batch, Company, Year."
    name_i = headers.index("name")
    company_i = len(headers) - 1 - headers[::-1].index("company")
    batch_i = headers.index("batch") if "batch" in headers else None
    year_i = headers.index("year") if "year" in headers else None
    if year_i is not None:
        company_i = max(
            (i for i, h in enumerate(headers) if h == "company" and i < year_i),
            default=company_i,
        )

    def cell(row, i):
        return row[i].strip() if i is not None and i < len(row) else ""

    entries = []
    for row in rows[1:]:
        name = " ".join(cell(row, name_i).split())
        year_match = re.search(r"\d{4}", cell(row, year_i))
        year = int(year_match.group()) if year_match else None
        for company in re.split(r"[,;]", cell(row, company_i)):
            company = " ".join(company.split())
            if name and company and company.lower() not in SKIP_COMPANIES:
                entries.append({
                    "name": name[:150], "batch": cell(row, batch_i)[:40],
                    "company": company[:150], "year": year,
                })
    return entries, None
