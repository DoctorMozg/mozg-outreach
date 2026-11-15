def make_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with a real text layer, built by hand so tests need no PDF writer."""
    text = "\n".join(
        f"BT /F1 11 Tf 50 {780 - 14 * n} Td ({line.replace('(', '').replace(')', '')}) Tj ET"
        for n, line in enumerate(lines)
    ).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(text), text),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


CV_LINES = [
    "Jane Example - Senior Python Engineer",
    "Berlin, Germany. English C1, German B2.",
    "Eight years building backend services in Python, FastAPI and PostgreSQL.",
    "Led the payments platform team at Acme Pay, moving billing to Kafka.",
    "Built ML feature pipelines with Airflow and dbt for fraud scoring.",
    "Mentored four engineers; ran the on-call rotation and incident reviews.",
]
