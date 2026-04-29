import unittest

from catalog_automation.google_sheets_io import (
    GoogleSheetsConfig,
    GoogleSheetsConfigError,
    GoogleSheetsParseError,
    a1_range_for_sheet,
    parse_spreadsheet_url,
    read_google_sheet,
    rows_from_csv,
    rows_from_values,
)


SPREADSHEET_ID = "1AbcDefGhijKlmNoPqrStuvWxyz123456789"


class TestGoogleSheetsIo(unittest.TestCase):
    def test_public_csv_reader_returns_catalogue_rows(self):
        calls = []

        def fake_fetch(url, headers, timeout_seconds):
            calls.append((url, headers, timeout_seconds))
            return "sku,price\nGP-RUBY-0001,125000\n,\n"

        rows = read_google_sheet(
            GoogleSheetsConfig(
                spreadsheet_id=SPREADSHEET_ID,
                value_range="'Input Raw'!A:ZZ",
                public_export=True,
                timeout_seconds=7,
            ),
            fetch_text=fake_fetch,
        )

        self.assertEqual(rows, [{"sku": "GP-RUBY-0001", "price": "125000"}])
        self.assertIn("/gviz/tq?", calls[0][0])
        self.assertIn("sheet=Input+Raw", calls[0][0])
        self.assertIn("range=A%3AZZ", calls[0][0])
        self.assertEqual(calls[0][1]["User-Agent"], "gempundit-catalog-automation/1.0")
        self.assertEqual(calls[0][2], 7)

    def test_generated_workbook_title_rows_are_skipped(self):
        rows = rows_from_values(
            [
                ["Input Raw Products"],
                [""],
                ["sku", "price"],
                ["GP-RUBY-0001", "125000"],
            ]
        )

        self.assertEqual(rows, [{"sku": "GP-RUBY-0001", "price": "125000"}])

    def test_parse_spreadsheet_url_extracts_id_and_gid(self):
        parsed = parse_spreadsheet_url(
            f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid=987654321"
        )

        self.assertEqual(parsed.spreadsheet_id, SPREADSHEET_ID)
        self.assertEqual(parsed.sheet_gid, "987654321")

    def test_a1_range_for_sheet_quotes_sheet_names(self):
        self.assertEqual(a1_range_for_sheet("Vendor's Raw Rows"), "'Vendor''s Raw Rows'!A:ZZ")

    def test_public_export_html_response_is_rejected(self):
        with self.assertRaises(GoogleSheetsParseError):
            rows_from_csv("<!doctype html><html><body>Sign in</body></html>")

    def test_invalid_spreadsheet_id_is_rejected(self):
        with self.assertRaises(GoogleSheetsConfigError):
            read_google_sheet(
                GoogleSheetsConfig(
                    spreadsheet_id="../bad",
                    value_range="'Input Raw'!A:ZZ",
                    public_export=True,
                ),
                fetch_text=lambda _url, _headers, _timeout: "",
            )


if __name__ == "__main__":
    unittest.main()
