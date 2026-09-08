import io
import importlib.util
import sys
import types
import unittest
import zipfile
from pathlib import Path


APP_DIR = Path(__file__).parents[1] / "functions" / "app"
sys.path.insert(0, str(APP_DIR))

boto3_stub = types.ModuleType("boto3")
boto3_stub.client = lambda *args, **kwargs: None
sys.modules.setdefault("boto3", boto3_stub)
botocore_stub = types.ModuleType("botocore")
botocore_config_stub = types.ModuleType("botocore.config")
botocore_config_stub.Config = object
sys.modules.setdefault("botocore", botocore_stub)
sys.modules.setdefault("botocore.config", botocore_config_stub)

storage_spec = importlib.util.spec_from_file_location("storage_for_validation_test", APP_DIR / "storage.py")
storage_for_test = importlib.util.module_from_spec(storage_spec)
storage_spec.loader.exec_module(storage_for_test)
detect_resume_type = storage_for_test.detect_resume_type
from validation import DOCX_TYPE, PDF_TYPE, ValidationError, normalize_phone, normalize_telegram, validate_application, validate_upload


class ValidationTests(unittest.TestCase):
    def test_normalizes_telegram_link(self):
        self.assertEqual(normalize_telegram("https://t.me/Edium_team"), "@Edium_team")

    def test_rejects_non_telegram_link(self):
        self.assertIsNone(normalize_telegram("https://example.com/person"))

    def test_normalizes_international_phone(self):
        self.assertEqual(normalize_phone("00 49 (151) 234-56789"), "+4915123456789")

    def test_rejects_phone_without_country_prefix(self):
        self.assertIsNone(normalize_phone("8 999 123 45 67"))

    def test_upload_rejects_oversized_file(self):
        with self.assertRaises(ValidationError) as error:
            validate_upload({"fileName": "cv.pdf", "fileSize": 10 * 1024 * 1024 + 1, "mediaType": PDF_TYPE})
        self.assertIn("resume", error.exception.field_errors)

    def test_application_rejects_short_motivation(self):
        with self.assertRaises(ValidationError) as error:
            validate_application(
                {
                    "uploadId": "1d0a3842-8fb8-4270-b552-2c28910f1538",
                    "firstName": "Анна",
                    "lastName": "Тестова",
                    "telegram": "@annatest",
                    "phone": "+4915123456789",
                    "motivation": "Слишком коротко",
                }
            )
        self.assertIn("motivation", error.exception.field_errors)

    def test_detects_pdf_by_content(self):
        self.assertEqual(detect_resume_type(b"%PDF-1.7\nsynthetic\n%%EOF\n"), PDF_TYPE)

    def test_rejects_renamed_executable(self):
        self.assertIsNone(detect_resume_type(b"MZ\x90\x00not really a pdf%%EOF"))

    def test_detects_docx_structure(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types />")
            archive.writestr("word/document.xml", '<?xml version="1.0"?><w:document />')
        self.assertEqual(detect_resume_type(buffer.getvalue()), DOCX_TYPE)


if __name__ == "__main__":
    unittest.main()
