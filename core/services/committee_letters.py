from io import BytesIO

from django.conf import settings
from django.contrib.staticfiles import finders
from django.utils import timezone
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas


ORGANIZATION_NAME = "TAMBUL HUSTLE YOUTH GROUP"
ORGANIZATION_TAGLINE = "Building a Brighter Future"
ORGANIZATION_ADDRESS = "P.O. Box 1109 - 30100, Eldoret"
PAGE_SIZE = A4
MARGIN_X = 54
BODY_TOP = 620
BODY_BOTTOM = 110
BLUE = colors.HexColor("#1d4ed8")
RED = colors.HexColor("#dc2626")
BLACK = colors.HexColor("#111827")


def committee_letter_pdf_filename(letter):
    reference = (letter.reference_number or "committee-letter").replace("/", "_")
    return f"{reference}.pdf"


def format_stamp_date(letter):
    official_date = letter.official_date
    return official_date.strftime("%d %b %Y").upper()


def letter_render_context(letter, official=False):
    return {
        "letter": letter,
        "official": official,
        "organization_name": ORGANIZATION_NAME,
        "organization_tagline": ORGANIZATION_TAGLINE,
        "organization_address": ORGANIZATION_ADDRESS,
        "stamp_date": format_stamp_date(letter),
        "show_stamp": official and letter.is_approved_for_pdf,
    }


def generate_committee_letter_pdf(letter):
    buffer = BytesIO()
    encryption = None
    if letter.is_approved_for_pdf:
        encryption = StandardEncryption(
            "",
            ownerPassword=letter.verification_code,
            canPrint=1,
            canModify=0,
            canCopy=0,
            canAnnotate=0,
        )

    pdf = canvas.Canvas(buffer, pagesize=PAGE_SIZE, encrypt=encryption)
    width, height = PAGE_SIZE
    _draw_page_frame(pdf, letter, width, height)

    y = height - 175
    y = _draw_metadata(pdf, letter, y, width)
    y -= 18
    y = _draw_recipient(pdf, letter, y, width)
    y -= 14

    pdf.setFillColor(BLACK)
    pdf.setFont("Helvetica", 10)
    pdf.drawString(MARGIN_X, y, letter.salutation)
    y -= 30

    pdf.setFont("Helvetica-Bold", 10.5)
    subject_text = f"RE: {letter.subject.upper()}"
    y = _draw_wrapped_text(
        pdf,
        subject_text,
        MARGIN_X,
        y,
        width - (MARGIN_X * 2),
        "Helvetica-Bold",
        10.5,
        15,
    )
    underline_y = y + 12
    pdf.setStrokeColor(BLACK)
    pdf.line(MARGIN_X, underline_y, min(width - MARGIN_X, MARGIN_X + 410), underline_y)
    y -= 8

    y = _draw_body_text(pdf, letter, y, width)

    if y < 275:
        _draw_footer(pdf, letter, width)
        pdf.showPage()
        _draw_page_frame(pdf, letter, width, height)
        y = BODY_TOP

    y -= 8
    pdf.setFillColor(BLACK)
    pdf.setFont("Helvetica", 10)
    pdf.drawString(MARGIN_X, y, letter.closing_phrase)
    y -= 24
    y = _draw_signature_area(pdf, letter, y, width)

    _draw_footer(pdf, letter, width)
    pdf.save()

    buffer.seek(0)
    return buffer.getvalue()


def _draw_page_frame(pdf, letter, width, height):
    _draw_letterhead(pdf, width, height)
    if not letter.is_approved_for_pdf:
        _draw_draft_watermark(pdf, width, height)


def _draw_letterhead(pdf, width, height):
    logo_path = finders.find("images/logo.png")
    if logo_path:
        pdf.drawImage(
            ImageReader(logo_path),
            MARGIN_X,
            height - 96,
            width=58,
            height=58,
            preserveAspectRatio=True,
            mask="auto",
        )

    optional_contact = " | ".join(
        value
        for value in [
            getattr(settings, "TAMBUL_LETTERHEAD_PHONE", "").strip(),
            getattr(settings, "TAMBUL_LETTERHEAD_EMAIL", "").strip(),
        ]
        if value
    )

    pdf.setFillColor(BLACK)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(MARGIN_X + 75, height - 52, ORGANIZATION_NAME)
    pdf.setFont("Helvetica-Bold", 9.5)
    pdf.setFillColor(RED)
    pdf.drawString(MARGIN_X + 75, height - 68, ORGANIZATION_TAGLINE)
    pdf.setFont("Helvetica", 9.2)
    pdf.setFillColor(BLACK)
    pdf.drawString(MARGIN_X + 75, height - 84, ORGANIZATION_ADDRESS)
    if optional_contact:
        pdf.drawRightString(width - MARGIN_X, height - 84, optional_contact)

    pdf.setStrokeColor(BLUE)
    pdf.setLineWidth(1.5)
    pdf.line(MARGIN_X, height - 116, width - MARGIN_X, height - 116)


def _draw_metadata(pdf, letter, y, width):
    pdf.setFillColor(BLACK)
    pdf.setFont("Helvetica", 10)
    pdf.drawString(MARGIN_X, y, f"Reference: {letter.reference_number}")
    pdf.drawRightString(
        width - MARGIN_X,
        y,
        f"Date: {letter.letter_date.strftime('%B')} {letter.letter_date.day}, {letter.letter_date.year}",
    )
    return y


def _draw_recipient(pdf, letter, y, width):
    lines = [letter.recipient_name]
    if letter.recipient_position:
        lines.append(letter.recipient_position)
    if letter.recipient_address:
        lines.extend(letter.recipient_address.splitlines())

    pdf.setFillColor(BLACK)
    for line in lines:
        y = _draw_wrapped_text(
            pdf,
            line,
            MARGIN_X,
            y,
            width - (MARGIN_X * 2),
            "Helvetica",
            10,
            14,
        )
    return y


def _draw_body_text(pdf, letter, y, width):
    for paragraph in letter.body.splitlines():
        if not paragraph.strip():
            y -= 10
            continue

        if y < BODY_BOTTOM:
            _draw_footer(pdf, letter, width)
            pdf.showPage()
            _draw_page_frame(pdf, letter, width, PAGE_SIZE[1])
            y = BODY_TOP

        y = _draw_wrapped_text(
            pdf,
            paragraph,
            MARGIN_X,
            y,
            width - (MARGIN_X * 2),
            "Helvetica",
            10,
            15,
        )
        y -= 5
    return y


def _draw_wrapped_text(pdf, text, x, y, max_width, font_name, font_size, leading):
    lines = simpleSplit(text or "", font_name, font_size, max_width) or [""]
    pdf.setFont(font_name, font_size)
    for line in lines:
        pdf.drawString(x, y, line)
        y -= leading
    return y


def _draw_signature_area(pdf, letter, y, width):
    signature_path = _file_path(
        letter.signatory.signature_image if letter.signatory else None
    ) or finders.find("images/signature.png")
    content_right = width - MARGIN_X
    signature_width = float(getattr(settings, "LETTER_SIGNATURE_WIDTH", 2.85 * inch))
    signature_height = float(getattr(settings, "LETTER_SIGNATURE_HEIGHT", 0.58 * inch))
    signature_x = content_right - signature_width
    signature_y = y - signature_height + 5

    if letter.is_approved_for_pdf and signature_path:
        pdf.drawImage(
            _cropped_signature_reader(signature_path),
            signature_x,
            signature_y,
            width=signature_width,
            height=signature_height,
            preserveAspectRatio=True,
            mask="auto",
        )
    else:
        pdf.line(signature_x, y - 10, content_right, y - 10)
        signature_y = y - 18

    pdf.setFillColor(BLACK)
    pdf.setFont("Helvetica-Bold", 10)
    name_y = signature_y - 10
    pdf.drawRightString(content_right, name_y, letter.signatory_name.upper())
    pdf.setFont("Helvetica", 10)
    position_y = name_y - 14
    pdf.drawRightString(content_right, position_y, letter.signatory_position)

    stamp_bottom = position_y
    if letter.is_approved_for_pdf:
        stamp_path = _file_path(letter.signatory.stamp_image if letter.signatory else None)
        if not stamp_path:
            stamp_path = finders.find("images/stamp.jpg")
        if stamp_path:
            stamp_width = float(getattr(settings, "LETTER_STAMP_WIDTH", 1.9 * inch))
            stamp_height = float(getattr(settings, "LETTER_STAMP_HEIGHT", 1.39 * inch))
            stamp_gap = float(getattr(settings, "LETTER_STAMP_SIGNATURE_GAP", 0.12 * inch))
            stamp_x = signature_x - stamp_gap - stamp_width
            stamp_y = signature_y + ((signature_height - stamp_height) / 2)
            _draw_stamp_with_date(pdf, stamp_path, stamp_x, stamp_y, letter)
            stamp_bottom = stamp_y

    return min(stamp_bottom, position_y) - 18


def _draw_stamp_with_date(pdf, stamp_path, x, y, letter):
    stamp_width = float(getattr(settings, "LETTER_STAMP_WIDTH", 1.9 * inch))
    stamp_height = float(getattr(settings, "LETTER_STAMP_HEIGHT", 1.39 * inch))
    date_x_offset = float(getattr(settings, "LETTER_STAMP_DATE_X_OFFSET", 1.06 * inch))
    date_y_offset = float(getattr(settings, "LETTER_STAMP_DATE_Y_OFFSET", 0.72 * inch))
    date_font_size = float(getattr(settings, "LETTER_STAMP_DATE_FONT_SIZE", 7.2))

    pdf.drawImage(
        ImageReader(stamp_path),
        x,
        y,
        width=stamp_width,
        height=stamp_height,
        preserveAspectRatio=True,
        mask="auto",
    )
    pdf.setFillColor(BLACK)
    pdf.setFont("Helvetica-Bold", date_font_size)
    pdf.drawCentredString(
        x + date_x_offset,
        y + date_y_offset,
        format_stamp_date(letter),
    )


def _draw_footer(pdf, letter, width):
    page_number = pdf.getPageNumber()
    pdf.setStrokeColor(colors.HexColor("#e5e7eb"))
    pdf.setLineWidth(0.5)
    pdf.line(MARGIN_X, 78, width - MARGIN_X, 78)
    pdf.setFillColor(colors.HexColor("#475569"))
    pdf.setFont("Helvetica", 8)
    pdf.drawString(
        MARGIN_X,
        62,
        "This letter was generated through the Tambul Hustle Portal.",
    )
    pdf.drawString(MARGIN_X, 48, f"Verification Code: {letter.verification_code}")
    pdf.drawRightString(width - MARGIN_X, 48, f"Page {page_number}")
    pdf.setFillColor(BLUE)
    pdf.drawRightString(width - MARGIN_X, 62, ORGANIZATION_TAGLINE)


def _draw_draft_watermark(pdf, width, height):
    pdf.saveState()
    pdf.translate(width / 2, height / 2)
    pdf.rotate(34)
    pdf.setFillColor(colors.Color(0.75, 0.05, 0.05, alpha=0.12))
    pdf.setFont("Helvetica-Bold", 36)
    pdf.drawCentredString(0, 0, "DRAFT - NOT OFFICIALLY APPROVED")
    pdf.restoreState()


def _file_path(file_field):
    if not file_field:
        return None
    try:
        if not file_field.name:
            return None
        return file_field.path
    except (NotImplementedError, ValueError):
        return None


def _cropped_signature_reader(signature_path):
    try:
        image = Image.open(signature_path).convert("RGBA")
        alpha = image.getchannel("A")
        threshold = int(getattr(settings, "LETTER_SIGNATURE_ALPHA_THRESHOLD", 5))
        padding = int(getattr(settings, "LETTER_SIGNATURE_CROP_PADDING", 24))
        mask = alpha.point(lambda pixel: 255 if pixel > threshold else 0)
        bbox = mask.getbbox()
        if not bbox:
            return ImageReader(signature_path)

        left, top, right, bottom = bbox
        cropped = image.crop(
            (
                max(left - padding, 0),
                max(top - padding, 0),
                min(right + padding, image.width),
                min(bottom + padding, image.height),
            )
        )
        buffer = BytesIO()
        cropped.save(buffer, format="PNG")
        buffer.seek(0)
        return ImageReader(buffer)
    except Exception:
        return ImageReader(signature_path)
