import pytest

pytestmark = pytest.mark.django_db


def test_the_help_link_serves_the_configured_pdf(client, event, settings, tmp_path):
    pdf = tmp_path / "quickref.pdf"
    pdf.write_bytes(b"%PDF-1.4 quick reference")
    settings.QUICKREF_PDF = pdf

    response = client.get("/quickref.pdf")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert b"".join(response.streaming_content) == b"%PDF-1.4 quick reference"


def test_a_missing_pdf_is_404(client, event, settings, tmp_path):
    settings.QUICKREF_PDF = tmp_path / "missing.pdf"

    assert client.get("/quickref.pdf").status_code == 404
