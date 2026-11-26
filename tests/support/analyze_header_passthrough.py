"""Run analyze_pdf_file on a PDF whose page text is the supplied header.

chat_complete is not the hosted model. It is a stand-in for the extraction
rules in app/services/prompts.py (SCOUT_SYSTEM_PROMPT and ANALYST_SYSTEM_PROMPT):
the model must return JSON whose title and entity come from the document
header. The stand-in copies that header out of the user prompt the real
templates build (SCOUT_USER_PROMPT / ANALYST_USER_PROMPT) and returns it as
JSON. Every later step is the product code: JSON parsing, entity consistency,
scoring, and the response dict.
"""

import importlib.util
import json
import logging
import sys
import types

logging.basicConfig(level=logging.ERROR, stream=sys.stderr)

ROOT = "/agent/repos/genome_chrome/form-genome-structured-cloudrun"
sys.path.insert(0, ROOT)

# analyze.py is imported by app.api, whose package init also imports the batch
# API and its database driver. Load the analyzer module by itself.
import app  # noqa: E402

api_pkg = types.ModuleType("app.api")
api_pkg.__path__ = [ROOT + "/app/api"]
api_pkg.__package__ = "app.api"
sys.modules["app.api"] = api_pkg

from pypdf import PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)


def make_pdf(path, text):
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    page = writer.pages[0]
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    contents = DecodedStreamObject()
    contents.set_data(stream)
    page[NameObject("/Contents")] = writer._add_object(contents)
    with open(path, "wb") as handle:
        writer.write(handle)


def header_from_prompt(user):
    """Return the first document line placed into the real user templates."""
    marker = "Return JSON:"
    head = user[: user.rfind(marker)] if marker in user else user
    blocks = [block.strip() for block in head.split("\n\n") if block.strip()]
    document = blocks[-1] if blocks else ""
    for line in document.splitlines():
        line = line.strip()
        if not line or line.startswith("--- PAGE"):
            continue
        return line
    return ""


def main():
    payload = sys.argv[1]
    mode = sys.argv[2]
    pdf_path = sys.argv[3]
    filename = sys.argv[4]
    url = sys.argv[5]
    make_pdf(pdf_path, payload)

    import app.services.llm_router as llm_router

    spec = importlib.util.spec_from_file_location(
        "app.api.analyze", ROOT + "/app/api/analyze.py"
    )
    analyze = importlib.util.module_from_spec(spec)
    sys.modules["app.api.analyze"] = analyze
    spec.loader.exec_module(analyze)

    state = {"prompt_contained_payload": False, "extracted_header": ""}

    def chat_complete(messages, **kwargs):
        user = next(m["content"] for m in reversed(messages) if m.get("role") == "user")
        header = header_from_prompt(user)
        state["extracted_header"] = header
        state["prompt_contained_payload"] = state["prompt_contained_payload"] or (
            payload in user
        )
        base = {
            "complexity_score": 5,
            "total_field_count": 1,
            "signature_required": False,
            "signature_count": 0,
            "confidence_tier": "High",
        }
        if mode == "omit-title":
            body = {**base, "entity": "Unknown", "confidence_tier": "Low"}
        elif mode == "pretty-title":
            # Extra key the response spreads through **final_res. getFormDisplayName
            # prefers pretty_title over title.
            body = {
                **base,
                "title": "Harmless Form",
                "entity": "Harmless Org",
                "pretty_title": header,
            }
        else:
            body = {**base, "title": header, "entity": header}
        return json.dumps(body)

    llm_router.chat_complete = chat_complete
    analyze.chat_complete = chat_complete
    result = analyze.analyze_pdf_file(pdf_path, filename, url=url)
    result.pop("preview", None)
    result["prompt_contained_payload"] = state["prompt_contained_payload"]
    result["extracted_header"] = state["extracted_header"]
    json.dump(result, sys.stdout, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
