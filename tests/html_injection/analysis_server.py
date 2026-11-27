"""Local stand-in for the attacker's PDF host plus the product's analyze API.

The PDF bytes are extracted by the product's analyze_pdf_file (pypdf, clean_text,
prompt assembly, json.loads). chat_complete is not the hosted model: the product
prompts tell that model to copy the document's title, purpose, and the other
free-text fields into JSON, and no product code strips markup from those strings.
This stand-in returns only exact slices of the user prompt it was given, so a
field can contain HTML only when that HTML survived extraction into the prompt.
"""

import json
import sys
from pathlib import Path

from flask import Flask, send_file

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "form-genome-structured-cloudrun"))

from app.api.analyze import analyze_bp  # noqa: E402
import app.api.analyze as analyze_module  # noqa: E402

PAYLOADS = json.loads((Path(__file__).parent / "payloads.json").read_text())
FIELD_ORDER = [
    "TITLE",
    "PURPOSE",
    "ACTION",
    "ENTITY",
    "ENTITY_NAME",
    "VERTICAL",
    "SUB",
    "PAYMENT",
    "SIGNER",
    "PROC",
    "TIER",
    "SOURCE",
]
FIXTURE_NAMES = {
    "breakout": "breakout.pdf",
    "https": "https-link.pdf",
    "javascript": "javascript-link.pdf",
}


def pdf_bytes(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 36 720 Td ({escaped}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def document_text(case: dict) -> str:
    parts = []
    for name in FIELD_ORDER:
        value = case[name]
        if f"[[/{name}]]" in value or f"[[{name}]]" in value:
            raise SystemExit(f"payload delimiter collides with {name}")
        parts.append(f"[[{name}]]{value}[[/{name}]]")
    return " ".join(parts)


def build_fixtures(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    written = {}
    for case_name, filename in FIXTURE_NAMES.items():
        text = document_text(PAYLOADS[case_name])
        path = directory / filename
        path.write_bytes(pdf_bytes(text))
        written[filename] = path
    return written


def _slice_field(prompt: str, name: str) -> str:
    start_marker = f"[[{name}]]"
    end_marker = f"[[/{name}]]"
    start = prompt.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{name} was not present in the analysis prompt")
    start += len(start_marker)
    end = prompt.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"{name} was truncated in the analysis prompt")
    return prompt[start:end]


def echo_document_fields(messages, **kwargs):
    """Return the JSON object the analyst prompt asks for, using prompt slices."""
    user = next(message["content"] for message in messages if message.get("role") == "user")
    body = {
        "title": _slice_field(user, "TITLE"),
        "entity": _slice_field(user, "ENTITY"),
        "entity_name": _slice_field(user, "ENTITY_NAME"),
        "complexity_score": 1,
        "total_field_count": 1,
        "signature_required": False,
        "signature_count": 0,
        "nigo_score": 1,
        "form_purpose": _slice_field(user, "PURPOSE"),
        "action_type": _slice_field(user, "ACTION"),
        "confidence_tier": _slice_field(user, "TIER"),
        "industry_vertical": _slice_field(user, "VERTICAL"),
        "industry_subvertical": _slice_field(user, "SUB"),
        "payment_amount": _slice_field(user, "PAYMENT"),
        "estimated_signer_time": _slice_field(user, "SIGNER"),
        "estimated_processing_time": _slice_field(user, "PROC"),
        "source_url": _slice_field(user, "SOURCE"),
        "notarization_required": False,
        "attachments_required": False,
        "attachment_count": 0,
        "payment_required": False,
        "identification_required": False,
        "conditional_logic": False,
        "third_party_involved": False,
        "witnesses_required": False,
        "deadlines_present": False,
    }
    for value in body.values():
        if isinstance(value, str) and value not in user:
            raise RuntimeError("model stand-in emitted a string that was not in the prompt")
    return json.dumps(body)


def create_app(fixture_dir: Path) -> Flask:
    analyze_module.chat_complete = echo_document_fields
    app = Flask(__name__)
    app.register_blueprint(analyze_bp)

    @app.get("/fixtures/<name>")
    def fixture(name):
        path = fixture_dir / name
        if not path.is_file():
            return {"error": "missing fixture"}, 404
        return send_file(path, mimetype="application/pdf")

    return app


def main():
    fixture_dir = Path(__file__).parent / "_fixtures"
    build_fixtures(fixture_dir)
    app = create_app(fixture_dir)
    app.run(host="127.0.0.1", port=8080, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
