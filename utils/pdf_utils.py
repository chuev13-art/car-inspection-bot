import os
import base64
from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    from weasyprint import HTML
except Exception:
    HTML = None


def encode_image_to_data_uri(path: str) -> str:
    """Encode an image file to a data URI (base64).

    Returns an empty string on failure.
    """
    if not os.path.exists(path):
        return ""
    mime = "image/png"
    if path.lower().endswith(".svg"):
        mime = "image/svg+xml"
    try:
        with open(path, "rb") as f:
            data = f.read()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return ""


def render_template(template_path: str, context: dict) -> str:
    """Render a Jinja2 template located in templates/ directory.

    `template_path` may be either a full path or a path relative to the repo root.
    """
    # If a full path is provided and points to a file inside templates/, use that.
    templates_dir = os.path.join(os.getcwd(), "templates")
    loader = FileSystemLoader(templates_dir)
    env = Environment(loader=loader, autoescape=select_autoescape(["html", "xml"]))
    # template_path should be relative to templates/ (e.g. "report.html")
    tpl_name = os.path.basename(template_path)
    template = env.get_template(tpl_name)
    return template.render(**context)


def generate_pdf_from_html(html: str, output_path: str) -> None:
    """Generate a PDF from HTML string using WeasyPrint.

    Raises RuntimeError if weasyprint is unavailable.
    """
    if HTML is None:
        raise RuntimeError("weasyprint is not installed or failed to import")
    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    HTML(string=html).write_pdf(output_path)


def create_export(report_data: dict, template_path: str, output_path: str) -> str:
    """High-level helper: render template with report_data and write PDF to output_path.

    Returns the output_path on success.
    """
    # Prepare logo data uri if not provided
    if "logo_data_uri" not in report_data or not report_data.get("logo_data_uri"):
        # Default location
        default_logo = os.path.join(os.getcwd(), "assets", "logo.png")
        report_data["logo_data_uri"] = encode_image_to_data_uri(default_logo)

    html = render_template(template_path, report_data)
    generate_pdf_from_html(html, output_path)
    return output_path
