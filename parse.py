"""
Main parser element for invoice PDF documents
"""

import json
import logging
from pathlib import Path
import re
import sys
from typing import List, Optional
import warnings

import pdfplumber
from custom_parsers import parse_with_custom_parser, detect_vendor

# Suppress Pillow warnings about invalid ICC profiles
warnings.filterwarnings("ignore", message=".*Invalid profile.*")
warnings.filterwarnings("ignore", category=UserWarning, module="PIL")

# Suppress logging noise from pdfminer
logging.getLogger("pdfminer").setLevel(logging.ERROR)


def decode_cid_references(text: str) -> str:
    """
    Decode PDF CID (Character ID) references to actual characters.

    PDFs sometimes use CID notation like (cid:54) when character encoding fails.
    This maps common CID codes to their actual characters based on standard fonts.
    """
    if not text or "(cid:" not in text:
        return text

    # Common CID to character mappings (based on typical PDF fonts)
    cid_map = {
        "34": '"',  # quotation mark
        "35": "#",  # hash/number sign
        "43": "+",  # plus
        "44": ",",  # comma
        "45": "-",  # hyphen
        "47": "/",  # slash
        "48": "0",
        "49": "1",
        "50": "2",
        "51": "3",
        "52": "4",
        "53": "5",
        "54": "6",  # appears in Yafa as (cid:54) = 6
        "55": "7",  # appears in Yafa as (cid:55) = 7
        "56": "8",
        "57": "9",
        "58": ":",  # colon
        "65": "A",
        "66": "B",
        "67": "C",
        "68": "D",
        "69": "E",
        "70": "F",
        "71": "G",  # appears in Yafa as (cid:71) = g/G
        "72": "H",  # appears in Yafa as (cid:72) = H
        "73": "I",
        "74": "J",
        "75": "K",
        "76": "L",
        "77": "M",
        "78": "N",  # appears in Yafa as (cid:78) = N
        "79": "O",
        "80": "P",
        "81": "Q",
        "82": "R",
        "83": "S",
        "84": "T",
        "85": "U",  # appears in Yafa as (cid:85) = U
        "86": "V",  # appears in Yafa as (cid:86) = V
        "87": "W",
        "88": "X",
        "89": "Y",
        "90": "Z",
        # Lowercase letters
        "97": "a",
        "98": "b",
        "99": "c",
        "100": "d",
        "101": "e",
        "102": "f",
        "103": "g",
        "104": "h",
        "105": "i",
        "106": "j",
        "107": "k",
        "108": "l",
        "109": "m",
        "110": "n",
        "111": "o",
        "112": "p",
        "113": "q",
        "114": "r",
        "115": "s",
        "116": "t",
        "117": "u",
        "118": "v",
        "119": "w",
        "120": "x",
        "121": "y",
        "122": "z",
        # Special characters
        "192": "À",  # A with grave accent
    }

    # Replace all CID references
    def replace_cid(match: re.Match[str]) -> str:
        cid_num = match.group(1)
        return cid_map.get(cid_num, match.group(0))  # Return original if not found

    return re.sub(r"\(cid:(\d+)\)", replace_cid, text)


# Configuration: Set to False to remove 0-quantity rows from output
INCLUDE_ZERO_QUANTITY = True


class InvoiceParser:
    """Parse invoice PDFs and extract line item tables using local processing."""

    def __init__(self, pdf_path: str):
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

    def extract_line_items(self) -> List[dict]:
        """
        Extract line items from invoice PDF using pdfplumber's table detection.
        This works locally without any API calls.
        Processes all pages and handles multi-page invoices.
        """
        # First, try custom parsers for special formats
        custom_items = parse_with_custom_parser(
            str(self.pdf_path), INCLUDE_ZERO_QUANTITY
        )
        if custom_items:
            return custom_items

        # Fall back to standard table-based parsing
        return self._extract_table_based()

    def _extract_table_based(self) -> List[dict]:
        """Standard table-based extraction for most invoices."""
        line_items = []
        last_header = None  # Store header for continuation tables

        with pdfplumber.open(self.pdf_path) as pdf:
            for _, page in enumerate(pdf.pages, 1):
                tables = page.extract_tables()

                # Find the table with line items (typically has headers like Qty, Item#, SKU, etc.)
                for table in tables:
                    if not table or len(table) < 1:
                        continue

                    # Check if this table contains a line items header (might not be first row)
                    header_row_idx: Optional[int] = None
                    header_row: Optional[List[Optional[str]]] = None

                    # Look for the header row in the first few rows of the table
                    for idx, row in enumerate(table[:5]):  # Check first 5 rows
                        if self._is_line_items_table(row):
                            header_row = row
                            header_row_idx = idx
                            break

                    if header_row is not None and header_row_idx is not None:
                        last_header = header_row
                        # Process each row after the header
                        for row in table[header_row_idx + 1 :]:
                            item = self._parse_line_item_row(row, header_row)
                            if item:
                                line_items.append(item)
                    elif last_header:
                        # This is likely a continuation table without headers
                        # Try to intelligently map columns
                        for row in table:
                            if row and any(
                                cell for cell in row if cell
                            ):  # Has at least one non-empty cell
                                # Try parsing as continuation
                                item = self._parse_continuation_row(row, last_header)
                                if item:
                                    line_items.append(item)

        return line_items

    def _parse_continuation_row(
        self, row: List[Optional[str]], header: List[Optional[str]]
    ) -> Optional[dict]:
        """
        Parse a row from a continuation table (no headers).
        These tables may have different column counts, so we need to be more flexible.
        """
        if not row:
            return None

        # For continuation tables, columns might be shifted or have different counts
        # We'll try to intelligently match by looking for patterns

        # Try the direct mapping first (same column structure as header)
        if len(row) == len(header):
            return self._parse_line_item_row(row, header)

        # If different column counts, try to find the data by pattern matching
        # Typical order: [empty cols], qty, item#, sku, description, price, amount, [empty cols]

        # Filter out leading/trailing empty cells
        trimmed_row = []
        start_idx = 0
        end_idx = len(row)

        # Find first non-empty cell
        for i, cell in enumerate(row):
            if cell and str(cell).strip():
                start_idx = i
                break

        # Find last non-empty cell
        for i in range(len(row) - 1, -1, -1):
            if row[i] and str(row[i]).strip():
                end_idx = i + 1
                break

        trimmed_row = row[start_idx:end_idx]

        # If we have at least 5 columns (qty, item#, sku, description, price, amount is 6)
        if len(trimmed_row) >= 5:
            try:
                # Try to extract with flexible positioning
                # Pattern: qty (int), item# (alphanumeric), sku (alphanumeric),
                # description (text), price (float), amount (float)

                qty_str = trimmed_row[0] if len(trimmed_row) > 0 else None
                item_num = trimmed_row[1] if len(trimmed_row) > 1 else None
                sku = trimmed_row[2] if len(trimmed_row) > 2 else None
                description = trimmed_row[3] if len(trimmed_row) > 3 else None
                price = trimmed_row[4] if len(trimmed_row) > 4 else None
                amount = trimmed_row[5] if len(trimmed_row) > 5 else None

                # Parse quantity
                if qty_str:
                    try:
                        qty = int(float(str(qty_str).strip()))
                        # Skip 0-quantity rows if configured to do so
                        if not INCLUDE_ZERO_QUANTITY and qty == 0:
                            return None
                    except (ValueError, AttributeError):
                        return None
                else:
                    return None

                # Clean up fields
                if item_num:
                    item_num = decode_cid_references(str(item_num))
                    item_num = " ".join(item_num.split())
                    # Extract first item number if multiple are present
                    item_num = self._extract_first_item_number(item_num)
                if sku:
                    sku = decode_cid_references(str(sku))
                    sku = " ".join(sku.split())
                if description:
                    description = decode_cid_references(str(description))
                    description = " ".join(description.split())

                # Parse price and amount
                unit_price = None
                total_amount = None

                if price:
                    try:
                        # Decode CID references first (e.g., (cid:54)2.50 -> 62.50)
                        price_str = decode_cid_references(str(price))
                        unit_price = float(
                            price_str.replace("$", "").replace(",", "").strip()
                        )
                    except (ValueError, AttributeError):
                        pass

                if amount:
                    try:
                        # Decode CID references first (e.g., 12(cid:55).50 -> 127.50)
                        amount_str = decode_cid_references(str(amount))
                        total_amount = float(
                            amount_str.replace("$", "").replace(",", "").strip()
                        )
                    except (ValueError, AttributeError):
                        pass

                # Validate we have the minimum required fields
                if not all([item_num, sku, description]):
                    return None

                return {
                    "quantity": qty,
                    "item_number": item_num,
                    "sku": sku,
                    "product_description": description,
                    "unit_price": unit_price,
                    "total_amount": total_amount,
                }

            except (IndexError, ValueError, AttributeError):
                return None

        return None

    def _is_line_items_table(self, header_row: List[Optional[str]]) -> bool:
        """
        Determine if a table is the line items table by checking headers.
        This is flexible enough to work with different invoice formats.
        """
        if not header_row:
            return False

        # Convert to lowercase for case-insensitive matching
        headers = [str(h).lower() if h else "" for h in header_row]

        # Check for common line item table indicators
        indicators = [
            "qty",
            "quantity",
            "item",
            "sku",
            "product",
            "price",
            "amount",
            "total",
        ]
        matches = sum(
            1 for indicator in indicators if any(indicator in h for h in headers)
        )

        return matches >= 3  # At least 3 indicators present

    def _parse_line_item_row(
        self, row: List[Optional[str]], headers: List[Optional[str]]
    ) -> Optional[dict]:
        """
        Parse a single row from the line items table.
        This handles different column orders and formats.
        """
        if not row or len(row) < 4:
            return None

        # Create a mapping of header -> value
        row_dict = {}
        for i, header in enumerate(headers):
            if i < len(row) and header:
                row_dict[str(header).lower().strip()] = row[i]

        # Try to extract required fields (flexible column matching)
        try:
            qty = self._extract_quantity(row_dict)
            if qty is None:
                return None  # Skip rows with no quantity

            # Skip 0-quantity rows if configured to do so
            if not INCLUDE_ZERO_QUANTITY and qty == 0:
                return None

            item_num = self._extract_field(
                row_dict,
                [
                    "item#",
                    "item #",
                    "item",
                    "item_number",
                    "itemnumber",
                    "item no",
                    "item no.",
                ],
            )
            sku = self._extract_field(row_dict, ["sku", "item_code", "item code"])
            product = self._extract_field(
                row_dict,
                ["product", "description", "item_description", "item description"],
            )
            unit_price = self._extract_price(
                row_dict, ["price", "unit_price", "unit price", "price each"]
            )
            total = self._extract_price(
                row_dict, ["amount", "total", "total_amount", "line_total"]
            )

            # Clean up item_num: if it contains multiple numbers (e.g., "15294006/138 04006"),
            # take only the first one
            if item_num:
                item_num = self._extract_first_item_number(item_num)

            # For some invoices, item# and SKU might be the same or one might be missing
            # If we're missing one, try to use the other
            if not item_num and sku:
                item_num = sku
            elif not sku and item_num:
                sku = item_num

            if not all([item_num, sku, product]):
                return None

            return {
                "quantity": qty,
                "item_number": item_num,
                "sku": sku,
                "product_description": product,
                "unit_price": unit_price,
                "total_amount": total,
            }

        except (ValueError, KeyError, AttributeError, TypeError) as e:
            print(f"Warning: Could not parse row: {e}")
            return None

    def _extract_quantity(self, row_dict: dict) -> Optional[int]:
        """Extract quantity from row, trying multiple possible column names."""
        for key in ["qty", "quantity", "qnty", "shipped", "ordered"]:
            if key in row_dict and row_dict[key]:
                try:
                    value_str = str(row_dict[key]).strip()
                    if value_str:
                        return int(float(value_str))
                except (ValueError, AttributeError):
                    continue
        return None

    def _extract_field(self, row_dict: dict, possible_keys: List[str]) -> Optional[str]:
        """Extract a string field, trying multiple possible column names."""
        for key in possible_keys:
            if key in row_dict and row_dict[key]:
                value = str(row_dict[key]).strip()
                # Decode PDF CID references (e.g., (cid:54) -> 6)
                value = decode_cid_references(value)
                # Replace newlines and multiple spaces with single space for clean CSV output
                value = " ".join(value.split())
                if value and value.lower() not in ["none", "null", ""]:
                    return value
        return None

    def _extract_first_item_number(self, item_num: str) -> str:
        """
        Extract the first item number from a string that may contain multiple numbers.
        Example: "15294006/138 04006" -> "15294006"
        Example: "41821006/623 22006" -> "41821006"
        """
        if not item_num:
            return item_num

        # Split by forward slash and take the first part
        parts = item_num.split("/")
        if len(parts) > 1:
            # Return the first part, stripped of whitespace
            return parts[0].strip()

        return item_num

    def _extract_price(
        self, row_dict: dict, possible_keys: List[str]
    ) -> Optional[float]:
        """Extract a price field, trying multiple possible column names."""
        for key in possible_keys:
            if key in row_dict and row_dict[key]:
                try:
                    # Decode CID references first (e.g., (cid:54)2.50 -> 62.50)
                    value = decode_cid_references(str(row_dict[key]))
                    # Remove currency symbols and commas
                    value = value.strip().replace("$", "").replace(",", "")
                    if value:
                        return float(value)
                except (ValueError, AttributeError):
                    continue
        return None


# ============================================================================
# Swift App Integration - Command Line Interface
# ============================================================================


def convert_to_table_format(line_items: List[dict]) -> List[List[str]]:
    """
    Convert line items to table format for Swift app.

    Returns a table with headers + data rows:
    [
        ["Quantity", "Backorder", "Item Number", "SKU", "Description", "Unit Price", "Total"],
        ["5", "0", "ABC123", "ABC123", "Product Name", "10.00", "50.00"],
        ...
    ]
    """
    if not line_items:
        return []

    # Define headers
    headers = [
        "Quantity",
        "Backorder",
        "Item Number",
        "SKU",
        "Description",
        "Unit Price",
        "Total",
    ]

    # Convert each item to a row
    rows = [headers]
    for item in line_items:
        row = [
            str(item.get("quantity", "")),
            str(item.get("backorder", "")),
            str(item.get("item_number", "")),
            str(item.get("sku", "")),
            str(item.get("product_description", "")),
            f"{item.get('unit_price', 0):.2f}"
            if item.get("unit_price") is not None
            else "",
            f"{item.get('total_amount', 0):.2f}"
            if item.get("total_amount") is not None
            else "",
        ]
        rows.append(row)

    return rows


def main():
    """
    Main entry point for Swift app integration.

    Usage: parse.py <pdf_path> <output_json>

    Output JSON format:
    {
        "success": true,
        "vendor": "vendor_name" or "generic",
        "table": [
            ["Quantity", "Item Number", ...],
            ["5", "ABC123", ...],
            ...
        ],
        "raw_items": [
            {"quantity": 5, "item_number": "ABC123", ...},
            ...
        ]
    }
    """

    if len(sys.argv) != 3:
        print("Usage: parse.py <pdf_path> <output_json>", file=sys.stderr)
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2]

    try:
        # Detect vendor
        vendor = "generic"
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if pdf.pages:
                    text = pdf.pages[0].extract_text() or ""
                    detected = detect_vendor(text)
                    if detected:
                        vendor = detected
        except (OSError, ValueError, KeyError, AttributeError) as e:
            print(f"Warning: Could not detect vendor: {e}", file=sys.stderr)

        # Parse invoice
        parser = InvoiceParser(pdf_path)
        line_items = parser.extract_line_items()

        # Convert to table format
        table = convert_to_table_format(line_items)

        # Build response
        result = {
            "success": True,
            "vendor": vendor,
            "table": table,
            "raw_items": line_items,
            "item_count": len(line_items),
        }

        # Write results as JSON
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)

    except (FileNotFoundError, OSError, ValueError, KeyError) as e:
        # Write error response
        error_result = {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(error_result, f, indent=2)

        print(f"Error parsing invoice: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
