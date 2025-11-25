"""
Custom invoice parsers for specific vendors.

This module contains specialized parsing logic for invoice formats that
don't work well with the standard table-based parser. Each parser is designed
to handle the unique quirks and layout of a specific vendor's invoice format.
"""

import re
from typing import List, Optional
from decimal import Decimal, ROUND_HALF_UP
import pdfplumber


class ItoyaParser:
    """Parser for Itoya invoices - text-based format with multi-line descriptions."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Itoya invoice using text pattern matching.

        Format: Item Description UOM Ordered Shipped Back Ordered Price Amount
        Example: 10-9847-620 KOP - TAMENURI RADEN 'NAMI' LE EACH 2 2 0 1,680.00 3,360.00

        Descriptions span 2-3 lines:
        - Line 1: Item number + base description + quantities/prices
        - Line 2: Additional description details (always present)
        - Line 3: Optional continuation (if line 2 doesn't end description)
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                in_items_section = False
                i = 0

                while i < len(lines):
                    line = lines[i]

                    if "Item Description UOM" in line:
                        in_items_section = True
                        i += 1
                        continue

                    if in_items_section and (
                        "Subtotal" in line
                        or "Total" in line
                        or "Page:" in line
                        or "Net Invoice" in line
                    ):
                        break

                    if in_items_section and line.strip():
                        # Match any UOM (EACH, BOX, PACK, etc.)
                        match = re.match(
                            r"^([\d\-]+)\s+(.+?)\s+(?:EACH|BOX|PACK)\s+\d+\s+(\d+)\s+\d+\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})",
                            line,
                        )

                        if match:
                            item_num, description, shipped, price, amount = (
                                match.groups()
                            )

                            # Collect multi-line description
                            desc_parts = [description.strip()]

                            # Always grab line 2 (additional description)
                            if i + 1 < len(lines):
                                next_line = lines[i + 1].strip()
                                # Make sure it's not the next item
                                if next_line and not re.match(
                                    r"^[\d\-]+\s+", next_line
                                ):
                                    desc_parts.append(next_line)
                                    i += 1

                                    # Check if we need line 3
                                    # If line 2 doesn't end with closing paren, or line 3 starts with opening paren
                                    if i + 1 < len(lines):
                                        line3 = lines[i + 1].strip()
                                        if line3 and not re.match(
                                            r"^[\d\-]+\s+", line3
                                        ):
                                            # Line 3 is continuation if it starts with '(' or previous line doesn't have complete parens
                                            if line3.startswith("(") or (
                                                not next_line.endswith(")")
                                            ):
                                                desc_parts.append(line3)
                                                i += 1

                            full_description = " ".join(desc_parts)
                            qty = int(shipped)

                            if qty > 0 or include_zero_qty:
                                items.append(
                                    {
                                        "quantity": qty,
                                        "item_number": item_num.strip(),
                                        "sku": item_num.strip(),
                                        "product_description": full_description,
                                        "unit_price": float(price.replace(",", "")),
                                        "total_amount": float(amount.replace(",", "")),
                                    }
                                )

                    i += 1

        return items


class LuxuryBrandsParser:
    """Parser for Luxury Brands invoices - handles text-based line item format."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Luxury Brands invoice.

        Format (as of 2025):
        - Line 1: #. ITEM_CODE DESCRIPTION Qty $Rate $Amount
        - Line 2: More description (UOM = ea)

        Old format: Multi-line cells in tables (kept for backward compatibility)
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            pending_item_number = None  # Track item number from previous page

            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                i = 0

                while i < len(lines):
                    line = lines[i].strip()

                    # Handle pending item number from previous page
                    if pending_item_number and line and not re.match(r"^\d+\.", line):
                        # This line should be the continuation of the split item
                        line = pending_item_number + " " + line
                        pending_item_number = None

                    # Check if line is just a number at end of page (item split across pages)
                    if re.match(r"^\d+\.$", line):
                        # Save it for the next page
                        pending_item_number = line
                        i += 1
                        continue

                    # New format: Look for numbered items
                    # Pattern: 1. ITEM_CODE DESCRIPTION Qty $Rate $Amount
                    # Example: 1. BENU- 19.2.19.5.0 B BENU- 19.2.19.5.0 B - Tiger's Eye Talisman 1 $81.00 $81.00
                    # Note: Item code appears twice - once at start, then again in description
                    match = re.match(
                        r"^(\d+)\.\s+(.+?)\s+(\d+)\s+\$?([\d,.]+)\s+\$?([\d,.]+)$",
                        line,
                    )

                    if match:
                        line_num, full_text, qty_str, rate_str, amount_str = (
                            match.groups()
                        )

                        try:
                            qty = int(qty_str)
                            if qty == 0 and not include_zero_qty:
                                i += 1
                                continue

                            # Find where the item code is repeated (description starts)
                            parts = full_text.split()

                            # Look for the pattern where item code appears twice
                            item_code = parts[0] if parts else ""
                            description = full_text

                            for idx in range(1, len(parts)):
                                test_code = " ".join(parts[:idx])
                                remaining = " ".join(parts[idx:])

                                # Check if remaining text starts with the item code
                                if remaining.startswith(test_code):
                                    item_code = test_code
                                    description = remaining
                                    break
                            else:
                                # Fallback: take first 2-3 tokens as item code
                                item_code = (
                                    " ".join(parts[:2]) if len(parts) >= 2 else parts[0]
                                )
                                description = (
                                    " ".join(parts[2:]) if len(parts) > 2 else ""
                                )

                            # Get continuation line with UOM if present
                            full_description = description.strip()
                            if i + 1 < len(lines):
                                next_line = lines[i + 1].strip()
                                # If next line doesn't start with a number (not a new item)
                                if (
                                    not re.match(r"^\d+\.", next_line)
                                    and next_line
                                    and "Total" not in next_line
                                ):
                                    full_description += " " + next_line
                                    i += 1

                            items.append(
                                {
                                    "quantity": qty,
                                    "item_number": item_code,
                                    "sku": item_code,
                                    "product_description": full_description,
                                    "unit_price": float(rate_str.replace(",", "")),
                                    "total_amount": float(amount_str.replace(",", "")),
                                }
                            )
                            i += 1
                            continue
                        except ValueError:
                            pass

                    i += 1

            # Old format fallback: Try table extraction
            if not items:
                for page in pdf.pages:
                    tables = page.extract_tables()

                    for table in tables:
                        if not table or len(table) < 3:
                            continue

                        # Find header row
                        header_row = None
                        for idx, row in enumerate(table[:5]):
                            if (
                                row
                                and "Quantity" in str(row)
                                and (
                                    "Item Code" in str(row) or "Description" in str(row)
                                )
                            ):
                                header_row = idx
                                break

                        if header_row is None:
                            continue

                        if header_row + 1 < len(table):
                            data_row = table[header_row + 1]

                            # Extract columns
                            quantities = data_row[0] if data_row[0] else ""
                            item_codes = (
                                data_row[1] if len(data_row) > 1 and data_row[1] else ""
                            )
                            descriptions = (
                                data_row[3] if len(data_row) > 3 and data_row[3] else ""
                            )
                            prices = (
                                data_row[8] if len(data_row) > 8 and data_row[8] else ""
                            )
                            amounts = (
                                data_row[11]
                                if len(data_row) > 11 and data_row[11]
                                else ""
                            )

                            # Split by newlines
                            qty_list = [
                                q.strip()
                                for q in str(quantities).split("\n")
                                if q.strip()
                            ]
                            item_list = [
                                i.strip()
                                for i in str(item_codes).split("\n")
                                if i.strip()
                            ]
                            desc_list = [
                                d.strip()
                                for d in str(descriptions).split("\n")
                                if d.strip()
                            ]
                            price_list = [
                                p.strip() for p in str(prices).split("\n") if p.strip()
                            ]
                            amount_list = [
                                a.strip() for a in str(amounts).split("\n") if a.strip()
                            ]

                            # Match them up
                            max_items = max(
                                len(qty_list), len(item_list), len(desc_list)
                            )

                            for i in range(max_items):
                                try:
                                    qty = int(qty_list[i]) if i < len(qty_list) else 0
                                    if qty == 0 and not include_zero_qty:
                                        continue

                                    item_num = (
                                        item_list[i] if i < len(item_list) else ""
                                    )
                                    description = (
                                        desc_list[i] if i < len(desc_list) else ""
                                    )
                                    price = (
                                        float(price_list[i].replace(",", ""))
                                        if i < len(price_list)
                                        else 0.0
                                    )
                                    amount = (
                                        float(amount_list[i].replace(",", ""))
                                        if i < len(amount_list)
                                        else 0.0
                                    )

                                    if item_num and description:
                                        items.append(
                                            {
                                                "quantity": qty,
                                                "item_number": item_num,
                                                "sku": item_num,
                                                "product_description": description,
                                                "unit_price": price,
                                                "total_amount": amount,
                                            }
                                        )
                                except (ValueError, IndexError):
                                    continue

        return items


class RediformParser:
    """Parser for Rediform invoices - handles corrupted text rendering."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Rediform invoice.

        Challenge: Text has duplicated/corrupted characters.
        Example: "IIINNNNVVVVOOOOIIIICCCCEEEE" instead of "INVOICE"
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                # Clean corrupted text (remove duplicate characters)
                cleaned = re.sub(r"(.)\1{2,}", r"\1", text)
                lines = cleaned.split("\n")

                for line in lines:
                    # Pattern: CODE DESCRIPTION UOM QTY PRICE AMOUNT
                    match = re.match(
                        r"^([A-Z0-9]{6,})\s+(.+?)\s+(EA|CS)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$",
                        line.strip(),
                    )

                    if match:
                        item_code, description, uom, qty_str, price_str, amount_str = (
                            match.groups()
                        )

                        try:
                            qty = int(float(qty_str))
                            if qty == 0 and not include_zero_qty:
                                continue

                            items.append(
                                {
                                    "quantity": qty,
                                    "item_number": item_code,
                                    "sku": item_code,
                                    "product_description": description.strip(),
                                    "unit_price": float(price_str),
                                    "total_amount": float(amount_str),
                                }
                            )
                        except ValueError:
                            continue

                    if "Subtotal" in line or "MERCHANDISE TOTAL" in line:
                        break

        return items


class TomsStudioParser:
    """Parser for Tom's Studio invoices - text-based with multi-line items."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Tom's Studio invoice.

        Format: Two different patterns:
        Pattern A (3 lines):
        - Line 1: SKU prefix (e.g., "PRO-INK-FOU-")
        - Line 2: Description + data on same line (e.g., "Mini Ink Collection - Jewel BOX-MINI-COL- 18 0% £ 9.91 £ 178.38")
        - Line 3: SKU suffix (e.g., "JEWEL")

        Pattern B (4 lines):
        - Line 1: Title start
        - Line 2: SKU_PART QTY TAX £PRICE £TOTAL
        - Line 3: Rest of title
        - Line 4: Full SKU
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                in_items = False
                i = 0

                while i < len(lines):
                    line = lines[i]

                    if "TITLE" in line and "SKU" in line and "QTY" in line:
                        in_items = True
                        i += 1
                        continue

                    if in_items:
                        if (
                            "SUBTOTAL" in line
                            or "SUB TOTAL" in line
                            or line.strip().startswith("TOTAL")
                        ):
                            # End of items section for this page, continue to next page
                            break

                        matched = False

                        # Try Pattern C: SKU prefix, data line with description, SKU suffix (3 lines)
                        # Example: "PRO-RES-ONE-" / "The 'One Dip Wonder' 10 0% £ 1.77 £ 17.70" / "DIP-WON-1"
                        # Or: "PRO-NIB-FOU-" / "Japanese Brush Tip 6 0% £ 10.20 £ 61.20" / "BRU"
                        if not matched and i + 2 < len(lines):
                            sku_prefix = line.strip()
                            data_line = lines[i + 1].strip()
                            sku_suffix = lines[i + 2].strip()

                            # Check if this looks like pattern C
                            # SKU prefix must be uppercase alphanumeric with hyphens
                            # SKU suffix can be uppercase alphanumeric (with or without hyphens)
                            # Both must be short and not contain data patterns
                            if (
                                re.match(r"^[A-Z0-9\-]+$", sku_prefix)
                                and len(sku_prefix) < 30
                                and "-"
                                in sku_prefix  # Prefix must have at least one hyphen
                                and re.match(r"^[A-Z0-9\-]+$", sku_suffix)
                                and len(sku_suffix) < 30
                                and not re.search(r"\d+\s+\d+%", sku_prefix)
                                and not re.search(r"\d+\s+\d+%", sku_suffix)
                            ):
                                data_match = re.search(
                                    r"^(.+?)\s+(\d+)\s+(\d+%)\s+[£$€]\s*([\d,]+\.?\d*)\s+[£$€]\s*([\d,]+\.?\d*)$",
                                    data_line,
                                )

                                if data_match:
                                    description = data_match.group(1).strip()
                                    qty_str = data_match.group(2)
                                    unit_price_str = data_match.group(4)
                                    total_str = data_match.group(5)

                                    try:
                                        qty = int(qty_str)
                                        if qty > 0 or include_zero_qty:
                                            full_sku = sku_prefix + sku_suffix
                                            items.append(
                                                {
                                                    "quantity": qty,
                                                    "item_number": full_sku,
                                                    "sku": full_sku,
                                                    "product_description": description,
                                                    "unit_price": float(
                                                        unit_price_str.replace(",", "")
                                                    ),
                                                    "total_amount": float(
                                                        total_str.replace(",", "")
                                                    ),
                                                }
                                            )
                                        i += 3
                                        matched = True
                                    except (ValueError, IndexError):
                                        pass

                        # Try Pattern D: Desc1, SKU prefix, Desc2 + data, SKU suffix, Desc3 (5 lines)
                        # Example: "Lumos Pro Duo" / "PRO-PEN-FL-" / "Pen 2 0% £ 37.36 £ 74.72" / "LUM3-DUO-MID" / "Matte Midnight"
                        if not matched and i + 4 < len(lines):
                            desc1 = line.strip()
                            sku_prefix = lines[i + 1].strip()
                            data_line = lines[i + 2].strip()
                            sku_suffix = lines[i + 3].strip()
                            desc3 = lines[i + 4].strip()

                            # Check if this looks like pattern D
                            if (
                                not re.search(
                                    r"\d+\s+\d+%", desc1
                                )  # desc1 shouldn't have data
                                and re.match(r"^[A-Z0-9\-]+$", sku_prefix)
                                and len(sku_prefix) < 30
                                and re.match(r"^[A-Z0-9\-]+$", sku_suffix)
                                and len(sku_suffix) < 30
                                and not re.search(r"\d+\s+\d+%", desc3)
                            ):  # desc3 shouldn't have data
                                data_match = re.search(
                                    r"^(.+?)\s+(\d+)\s+(\d+%)\s+[£$€]\s*([\d,]+\.?\d*)\s+[£$€]\s*([\d,]+\.?\d*)$",
                                    data_line,
                                )

                                if data_match:
                                    desc2 = data_match.group(1).strip()
                                    qty_str = data_match.group(2)
                                    unit_price_str = data_match.group(4)
                                    total_str = data_match.group(5)

                                    try:
                                        qty = int(qty_str)
                                        if qty > 0 or include_zero_qty:
                                            full_sku = sku_prefix + sku_suffix
                                            full_description = (
                                                f"{desc1} {desc2} {desc3}"
                                            )
                                            items.append(
                                                {
                                                    "quantity": qty,
                                                    "item_number": full_sku,
                                                    "sku": full_sku,
                                                    "product_description": full_description,
                                                    "unit_price": float(
                                                        unit_price_str.replace(",", "")
                                                    ),
                                                    "total_amount": float(
                                                        total_str.replace(",", "")
                                                    ),
                                                }
                                            )
                                        i += 5
                                        matched = True
                                    except (ValueError, IndexError):
                                        pass

                        # Try Pattern E: Desc1 + SKU prefix on same line, data-only line, Desc2 + SKU suffix on same line (3 lines)
                        # Example: "Lumos - Tips (pack of 3) PRO-BRU-FIB-TIP-" / "6 0% £ 1.16 £ 6.96" / "3 x Brush Fibre Tip LUM-X3"
                        if not matched and i + 2 < len(lines):
                            line1 = line.strip()
                            data_line = lines[i + 1].strip()
                            line3 = lines[i + 2].strip()

                            # Check if line1 ends with SKU-like pattern (allow periods in SKU)
                            line1_match = re.match(r"^(.+?)\s+([A-Z0-9\-\.]+)$", line1)

                            # Check if data_line is JUST numbers (qty, tax, prices - no description)
                            data_match = re.match(
                                r"^(\d+)\s+(\d+%)\s+[£$€]\s*([\d,]+\.?\d*)\s+[£$€]\s*([\d,]+\.?\d*)$",
                                data_line,
                            )

                            # Check if line3 has description followed by SKU suffix (allow periods)
                            line3_match = re.match(r"^(.+?)\s+([A-Z0-9\-\.]+)$", line3)

                            if line1_match and data_match and line3_match:
                                desc1 = line1_match.group(1)
                                sku_prefix = line1_match.group(2)
                                qty_str = data_match.group(1)
                                unit_price_str = data_match.group(3)
                                total_str = data_match.group(4)
                                desc2 = line3_match.group(1)
                                sku_suffix = line3_match.group(2)

                                # Verify SKU parts look valid (have hyphens, reasonable length)
                                if (
                                    len(sku_prefix) < 30
                                    and len(sku_suffix) < 30
                                    and "-" in sku_prefix
                                ):  # Only prefix needs hyphen (suffix might be like LUM-X3)
                                    try:
                                        qty = int(qty_str)
                                        if qty > 0 or include_zero_qty:
                                            full_sku = sku_prefix + sku_suffix
                                            full_description = f"{desc1} {desc2}"
                                            items.append(
                                                {
                                                    "quantity": qty,
                                                    "item_number": full_sku,
                                                    "sku": full_sku,
                                                    "product_description": full_description,
                                                    "unit_price": float(
                                                        unit_price_str.replace(",", "")
                                                    ),
                                                    "total_amount": float(
                                                        total_str.replace(",", "")
                                                    ),
                                                }
                                            )
                                        i += 3
                                        matched = True
                                    except (ValueError, IndexError):
                                        pass

                        # Try Pattern F: Desc1, SKU+data on same line, Desc2 (3 lines)
                        # Example: "Fine Fountain Pen Nib" / "PRO-NIB-FOU-FIN 3 0% £ 5.90 £ 17.70" / "Gold"
                        if not matched and i + 2 < len(lines):
                            desc1 = line.strip()
                            sku_data_line = lines[i + 1].strip()
                            desc2 = lines[i + 2].strip()

                            # Check if middle line has SKU + data pattern
                            sku_data_match = re.match(
                                r"^([A-Z0-9\-]+)\s+(\d+)\s+(\d+%)\s+[£$€]\s*([\d,]+\.?\d*)\s+[£$€]\s*([\d,]+\.?\d*)$",
                                sku_data_line,
                            )

                            # Validate: desc1 and desc2 should not have data patterns, SKU should have hyphens
                            if (
                                sku_data_match
                                and not re.search(r"\d+\s+\d+%", desc1)
                                and not re.search(r"\d+\s+\d+%", desc2)
                                and "-" in sku_data_match.group(1)
                                and len(desc1) > 3
                                and len(desc2) > 2
                            ):  # Ensure descriptions are substantial
                                sku = sku_data_match.group(1)
                                qty_str = sku_data_match.group(2)
                                unit_price_str = sku_data_match.group(4)
                                total_str = sku_data_match.group(5)

                                try:
                                    qty = int(qty_str)
                                    if qty > 0 or include_zero_qty:
                                        full_description = f"{desc1} {desc2}"
                                        items.append(
                                            {
                                                "quantity": qty,
                                                "item_number": sku,
                                                "sku": sku,
                                                "product_description": full_description,
                                                "unit_price": float(
                                                    unit_price_str.replace(",", "")
                                                ),
                                                "total_amount": float(
                                                    total_str.replace(",", "")
                                                ),
                                            }
                                        )
                                    i += 3
                                    matched = True
                                except (ValueError, IndexError):
                                    pass

                        if matched:
                            continue

                        # Check if current line has data (Pattern A: description + data on same line)
                        current_match = re.search(
                            r"(.+?)\s+([A-Z0-9\-]+)\s+(\d+)\s+(\d+%)\s+[£$€]\s*([\d,]+\.?\d*)\s+[£$€]\s*([\d,]+\.?\d*)$",
                            line.strip(),
                        )

                        if current_match:
                            # Pattern A: SKU spans 3 lines (prefix + middle + suffix)
                            # Example:
                            #   Line i-1: "PRO-INK-FOU-"
                            #   Line i:   "Mini Ink Collection - Jewel BOX-MINI-COL- 18 0% £ 9.91 £ 178.38"
                            #   Line i+1: "JEWEL"
                            description = current_match.group(1).strip()
                            sku_middle = current_match.group(2)
                            qty_str = current_match.group(3)
                            unit_price_str = current_match.group(5)
                            total_str = current_match.group(6)

                            # Validate: description must be substantial (>10 chars) and SKU middle must have hyphens
                            # This prevents Pattern A from matching lines that should be Pattern C
                            if not (len(description) > 10 and "-" in sku_middle):
                                i += 1
                                continue

                            try:
                                qty = int(qty_str)
                                if qty == 0 and not include_zero_qty:
                                    i += 1
                                    continue

                                # Get SKU parts from previous and next lines
                                sku_prefix = lines[i - 1].strip() if i > 0 else ""
                                sku_suffix = (
                                    lines[i + 1].strip() if i + 1 < len(lines) else ""
                                )

                                # Build full SKU by concatenating all three parts
                                sku_parts = []

                                # Add prefix if it looks like a SKU part (not a data line, not too long)
                                if (
                                    sku_prefix
                                    and not re.search(r"\d+\s+\d+%", sku_prefix)
                                    and len(sku_prefix) < 30
                                ):
                                    sku_parts.append(sku_prefix)

                                # Add middle part (always included)
                                sku_parts.append(sku_middle)

                                # Add suffix if it looks like a SKU part (not a data line, not too long)
                                if (
                                    sku_suffix
                                    and not re.match(r"^\d+\s+\d+%", sku_suffix)
                                    and len(sku_suffix) < 30
                                ):
                                    sku_parts.append(sku_suffix)

                                # Concatenate all parts (they already have hyphens)
                                full_sku = "".join(sku_parts)

                                items.append(
                                    {
                                        "quantity": qty,
                                        "item_number": full_sku,
                                        "sku": full_sku,
                                        "product_description": description,
                                        "unit_price": float(
                                            unit_price_str.replace(",", "")
                                        ),
                                        "total_amount": float(
                                            total_str.replace(",", "")
                                        ),
                                    }
                                )
                                i += 2  # Skip to line after SKU suffix
                                continue
                            except (ValueError, IndexError):
                                pass

                        # Check if next line has data (Pattern B: title, then data line)
                        if i + 1 < len(lines):
                            next_line = lines[i + 1]
                            match = re.match(
                                r"^([A-Z0-9\-]+)\s+(\d+)\s+(\d+%)\s+[£$€]\s*([\d,]+\.?\d*)\s+[£$€]\s*([\d,]+\.?\d*)$",
                                next_line.strip(),
                            )

                            if match:
                                sku_middle, qty_str, tax, unit_price_str, total_str = (
                                    match.groups()
                                )
                                title = line.strip()

                                try:
                                    qty = int(qty_str)
                                    if qty == 0 and not include_zero_qty:
                                        i += 2
                                        continue

                                    # Pattern B: SKU spans 4 lines
                                    # Example:
                                    #   Line i-1: "BUN-SET-GIFT-" (SKU prefix)
                                    #   Line i:   "Lumos Pro Duo Gift Set" (title)
                                    #   Line i+1: "LUM-DUO-WHO- 24 0% £ 54.82 £ 1,315.68" (SKU middle + data)
                                    #   Line i+2: "Ivy" (more title)
                                    #   Line i+3: "IVY-LN" (SKU suffix)

                                    # Build full SKU from all parts
                                    sku_parts = []

                                    # Add prefix from line before current (i-1)
                                    if i > 0:
                                        sku_prefix = lines[i - 1].strip()
                                        if (
                                            sku_prefix
                                            and not re.search(r"\d+\s+\d+%", sku_prefix)
                                            and len(sku_prefix) < 30
                                        ):
                                            sku_parts.append(sku_prefix)

                                    # Add middle part from data line (i+1)
                                    sku_parts.append(sku_middle)

                                    # Add suffix from line i+3
                                    if i + 3 < len(lines):
                                        sku_suffix = lines[i + 3].strip()
                                        if (
                                            sku_suffix
                                            and not re.match(r"^\d+\s+\d+%", sku_suffix)
                                            and len(sku_suffix) < 30
                                        ):
                                            sku_parts.append(sku_suffix)

                                    # Concatenate all SKU parts
                                    full_sku = "".join(sku_parts)

                                    # Build full title
                                    if i + 2 < len(lines):
                                        title += " " + lines[i + 2].strip()

                                    items.append(
                                        {
                                            "quantity": qty,
                                            "item_number": full_sku,
                                            "sku": full_sku,
                                            "product_description": title,
                                            "unit_price": float(
                                                unit_price_str.replace(",", "")
                                            ),
                                            "total_amount": float(
                                                total_str.replace(",", "")
                                            ),
                                        }
                                    )
                                    i += 4
                                    continue
                                except (ValueError, IndexError):
                                    pass

                    i += 1

        return items


class ColesParser:
    """Parser for Coles invoices - handles multi-line descriptions spanning 2-3 lines."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Coles invoice.

        Challenge: All items packed in single cells with newlines.
        Descriptions span 2-3 lines per item:
        - If line ends with " -" (dash only), nib size is on next line (3 lines total)
        - Otherwise, description is complete (2 lines total)
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()

                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    header_row = table[0]
                    if not (
                        "Item" in str(header_row) and "Description" in str(header_row)
                    ):
                        continue

                    if len(table) > 1:
                        data_row = table[1]

                        # Extract columns
                        # Coles format: Item | Description | Size | LE #'s | B/O | Order | Rate | Amount
                        item_codes = (
                            data_row[0] if len(data_row) > 0 and data_row[0] else ""
                        )
                        desc_raw = (
                            data_row[1] if len(data_row) > 1 and data_row[1] else ""
                        )
                        backorders = (
                            data_row[4] if len(data_row) > 4 and data_row[4] else ""
                        )
                        orders = (
                            data_row[5] if len(data_row) > 5 and data_row[5] else ""
                        )
                        rates = data_row[6] if len(data_row) > 6 and data_row[6] else ""
                        amounts = (
                            data_row[7] if len(data_row) > 7 and data_row[7] else ""
                        )

                        # Split item codes and filter out non-items
                        item_list = [
                            i.strip()
                            for i in str(item_codes).split("\n")
                            if i.strip()
                            and not i.strip().endswith("%")
                            and "DISCOUNT" not in i
                            and "UPS" not in i
                            and "Ground" not in i
                        ]

                        # Split raw description lines
                        desc_lines = [
                            d.strip()
                            for d in str(desc_raw).split("\n")
                            if d.strip() and "DISCOUNT" not in d and "UPS" not in d
                        ]

                        # Parse order quantities and backorders
                        order_list = [
                            o.strip()
                            for o in str(orders).split("\n")
                            if o.strip() and o.strip().isdigit()
                        ]
                        backorder_list = [
                            b.strip()
                            for b in str(backorders).split("\n")
                            if b.strip() and b.strip().isdigit()
                        ]
                        rate_list = [
                            r.strip()
                            for r in str(rates).split("\n")
                            if r.strip() and re.match(r"^[\d.]+$", r.strip())
                        ]
                        amount_list = [
                            a.strip()
                            for a in str(amounts).split("\n")
                            if a.strip() and re.match(r"^[\d.]+$", a.strip())
                        ]

                        # Combine multi-line descriptions
                        # Each item has 2-3 description lines that need to be concatenated
                        # Pattern: Line 1 (base model) + Line 2 (collection/variant) [+ Line 3 (nib size if separate)]
                        desc_list = []
                        desc_idx = 0
                        for _ in range(len(order_list)):
                            if desc_idx >= len(desc_lines):
                                desc_list.append("")
                                continue

                            # Every item has at least 2 lines
                            desc_parts = [desc_lines[desc_idx]]
                            desc_idx += 1

                            if desc_idx < len(desc_lines):
                                desc_parts.append(desc_lines[desc_idx])
                                desc_idx += 1

                                # Check if we need a 3rd line
                                # If line 2 ends with " -" and line 3 exists and is very short (nib size)
                                if (
                                    desc_parts[-1].endswith(" -")
                                    and desc_idx < len(desc_lines)
                                    and len(desc_lines[desc_idx]) <= 3
                                ):
                                    desc_parts.append(desc_lines[desc_idx])
                                    desc_idx += 1

                            desc_list.append(" ".join(desc_parts))

                        # Build line items
                        for i, qty_str in enumerate(order_list):
                            try:
                                qty = int(qty_str)
                                if qty == 0 and not include_zero_qty:
                                    continue

                                item_num = item_list[i] if i < len(item_list) else ""
                                description = desc_list[i] if i < len(desc_list) else ""
                                backorder = (
                                    int(backorder_list[i]) if i < len(backorder_list) else 0
                                )

                                price = (
                                    float(rate_list[i]) if i < len(rate_list) else 0.0
                                )
                                amount = (
                                    float(amount_list[i])
                                    if i < len(amount_list)
                                    else 0.0
                                )

                                if item_num:
                                    items.append(
                                        {
                                            "quantity": qty,
                                            "backorder": backorder,
                                            "item_number": item_num,
                                            "sku": item_num,
                                            "product_description": description,
                                            "unit_price": price,
                                            "total_amount": amount,
                                        }
                                    )
                            except (ValueError, IndexError):
                                continue

        return items


class LamyParser:
    """Parser for Lamy invoices - text-based with data then description."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Lamy invoice.

        Two formats supported:

        Format 1 (Sales Order - Old):
        - Header: Quantity Back Ordered Item Rate Amount
        - Line 1: QTY BACKORDER ITEMCODE $PRICE $AMOUNT
        - Line 2: DESCRIPTION

        Format 2 (Invoice - New):
        - Header: Quantity Item UPC Code Retail Price Level Cost Amount
        - Line 1: QTY ITEMCODE UPC RETAIL PRICE LEVEL COST AMOUNT
        - Line 2: PERCENTAGE (e.g., "10%")
        - Line 3: DESCRIPTION
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                in_items = False
                invoice_format = None  # Will be "old" or "new"
                i = 0

                while i < len(lines):
                    line = lines[i]

                    # Detect format based on header
                    if "Quantity" in line and "Item" in line:
                        in_items = True
                        if "Back Ordered" in line:
                            invoice_format = "old"
                        elif "UPC Code" in line:
                            invoice_format = "new"
                        i += 1
                        continue

                    if in_items:
                        if (
                            "SUBTOTAL" in line
                            or "SUB TOTAL" in line
                            or "Sub-Total" in line
                        ):
                            break

                        # Format 1 (Old): QTY BACKORDER ITEMCODE $PRICE $AMOUNT
                        if invoice_format == "old":
                            match = re.match(
                                r"^(\d+)\s+(\d+)\s+([A-Z]\d+[A-Z]*)\s+\$?([\d,.]+)\s+\$?([\d,.]+)$",
                                line.strip(),
                            )

                            if match:
                                qty_str, backorder_str, item_code, price_str, amount_str = (
                                    match.groups()
                                )
                                description = (
                                    lines[i + 1].strip() if i + 1 < len(lines) else ""
                                )

                                try:
                                    qty = int(qty_str)
                                    backorder = int(backorder_str)
                                    if qty == 0 and not include_zero_qty:
                                        i += 2
                                        continue

                                    items.append(
                                        {
                                            "quantity": qty,
                                            "backorder": backorder,
                                            "item_number": item_code,
                                            "sku": item_code,
                                            "product_description": description,
                                            "unit_price": float(
                                                price_str.replace(",", "")
                                            ),
                                            "total_amount": float(
                                                amount_str.replace(",", "")
                                            ),
                                        }
                                    )
                                    i += 2
                                    continue
                                except ValueError:
                                    pass

                        # Format 2 (New): QTY ITEMCODE UPC RETAIL PRICE LEVEL COST AMOUNT
                        elif invoice_format == "new":
                            # Pattern: QTY ITEMCODE UPC RETAIL_PRICE LEVEL COST AMOUNT
                            # Example: 2 L376 021274316352 $55.00 50% less $24.75 $49.50
                            # Item codes can include slashes: LZ50/1.1, LZ50BK/B, LZ50BK/EF
                            # Item codes can start with multiple letters: LZ50, L376
                            # Item codes can have mixed letters/numbers: L0A9DDSKF, L0A5DENEF
                            # Item codes can have hyphens: L471-2
                            match = re.match(
                                r"^(\d+)\s+([A-Z0-9]+(?:-\d+|/[A-Z0-9.]+)?)\s+\d+\s+\$?([\d,.]+)\s+.*?\s+\$?([\d,.]+)\s+\$?([\d,.]+)$",
                                line.strip(),
                            )

                            if match:
                                qty_str, item_code, _retail, price_str, amount_str = (
                                    match.groups()
                                )

                                # Skip the percentage line (e.g., "10%")
                                # Description is on the line after that
                                description = ""
                                if i + 2 < len(lines):
                                    description = lines[i + 2].strip()

                                try:
                                    qty = int(qty_str)
                                    if qty == 0 and not include_zero_qty:
                                        i += 3  # Skip data line, percentage line, description line
                                        continue

                                    items.append(
                                        {
                                            "quantity": qty,
                                            "item_number": item_code,
                                            "sku": item_code,
                                            "product_description": description,
                                            "unit_price": float(
                                                price_str.replace(",", "")
                                            ),
                                            "total_amount": float(
                                                amount_str.replace(",", "")
                                            ),
                                        }
                                    )
                                    i += 3  # Skip data line, percentage line, description line
                                    continue
                                except ValueError:
                                    pass

                    i += 1

        return items


class PilotParser:
    """Parser for Pilot invoices - text-based format."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Pilot invoice.

        Format: QTY QTY EA ITEMCODE DESCRIPTION PRICE TOTAL
        Example: 2 2 EA 21966 CUSTOM 74 FP SOFT FINE NIB LFOG 150.00 300.00
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")

                for line in lines:
                    match = re.match(
                        r"^(\d+)\s+\d+\s+EA\s+(\d+)\s+(.+?)\s+([\d.]+)\s+([\d.]+)$",
                        line.strip(),
                    )

                    if match:
                        qty_str, item_code, description, price_str, amount_str = (
                            match.groups()
                        )

                        try:
                            qty = int(qty_str)
                            if qty == 0 and not include_zero_qty:
                                continue

                            items.append(
                                {
                                    "quantity": qty,
                                    "item_number": item_code,
                                    "sku": item_code,
                                    "product_description": description.strip(),
                                    "unit_price": float(price_str),
                                    "total_amount": float(amount_str),
                                }
                            )
                        except ValueError:
                            continue

        return items


class MontblancParser:
    """Parser for Montblanc invoices - text-based format."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Montblanc invoice.

        Two formats:
        A) ARTICLE# DESCRIPTION QTY PC RSP PRICE TOTAL (all on one line)
        B) ARTICLE# QTY PC RSP PRICE TOTAL (description on following lines)

        Challenge: Need to detect which format and handle accordingly.
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                in_items = False
                i = 0

                while i < len(lines):
                    line = lines[i]

                    if "Article" in line and "Description" in line and "QTY" in line:
                        in_items = True
                        i += 1
                        continue

                    if not in_items:
                        i += 1
                        continue

                    # Skip footer/header lines
                    if re.match(
                        r"^(SUBTOTAL|Freight|TOTAL|Net Weight|Montblanc|Page\s*:)",
                        line.strip(),
                    ):
                        i += 1
                        continue

                    # Try Format A: Article with description on same line
                    match_a = re.match(
                        r"^(\d{5,})\s+(.+?)\s+(\d+)\s+PC\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$",
                        line.strip(),
                    )

                    # Try Format B: Article with just numbers (description follows)
                    match_b = re.match(
                        r"^(\d{5,})\s+(\d+)\s+PC\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$",
                        line.strip(),
                    )

                    if match_a:
                        # Format A: description is on the same line
                        (
                            item_code,
                            description,
                            qty_str,
                            _,  # rsp
                            unit_price_str,
                            total_str,
                        ) = match_a.groups()

                        try:
                            qty = int(qty_str)
                            if qty == 0 and not include_zero_qty:
                                i += 1
                                continue

                            items.append(
                                {
                                    "quantity": qty,
                                    "item_number": item_code,
                                    "sku": item_code,
                                    "product_description": description.strip(),
                                    "unit_price": float(
                                        unit_price_str.replace(",", "")
                                    ),
                                    "total_amount": float(total_str.replace(",", "")),
                                }
                            )

                            # Collect continuation lines (SERIAL NUMBER, etc.)
                            i += 1
                            while i < len(lines):
                                next_line = lines[i].strip()
                                # Stop if we hit another item or footer
                                if re.match(
                                    r"^(\d{5,}|SUBTOTAL|DELIVERY:|Page\s*:)", next_line
                                ):
                                    break
                                # Add continuation if it's not shipping info
                                if next_line and not re.match(
                                    r"^(Customer PO:|SO:|Tracking|Gold Unit)", next_line
                                ):
                                    items[-1]["product_description"] += " " + next_line
                                i += 1
                            continue

                        except ValueError:
                            i += 1
                            continue

                    elif match_b:
                        # Format B: description is on following lines
                        (
                            item_code,
                            qty_str,
                            _,  # rsp
                            unit_price_str,
                            total_str,
                        ) = match_b.groups()

                        try:
                            qty = int(qty_str)
                            if qty == 0 and not include_zero_qty:
                                i += 1
                                continue

                            # Collect description from following lines
                            description_parts = []
                            i += 1
                            while i < len(lines):
                                next_line = lines[i].strip()
                                # Stop if we hit another item or footer
                                if re.match(
                                    r"^(\d{5,}|SUBTOTAL|DELIVERY:|Page\s*:)", next_line
                                ):
                                    break
                                # Add line if it's not shipping/metadata
                                if next_line and not re.match(
                                    r"^(Customer PO:|SO:|Tracking|Gold Unit)", next_line
                                ):
                                    description_parts.append(next_line)
                                i += 1

                            items.append(
                                {
                                    "quantity": qty,
                                    "item_number": item_code,
                                    "sku": item_code,
                                    "product_description": " ".join(description_parts),
                                    "unit_price": float(
                                        unit_price_str.replace(",", "")
                                    ),
                                    "total_amount": float(total_str.replace(",", "")),
                                }
                            )
                            continue

                        except ValueError:
                            i += 1
                            continue

                    i += 1

        return items


class LighthouseParser:
    """Parser for Lighthouse invoices - table-based."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Lighthouse invoice.

        Challenge: Item codes shown as "FB_Item", actual codes in description.
        Format: "373976 - Notebook Medium (A5)..."
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()

                for table in tables:
                    if not table or len(table) < 3:
                        continue

                    header_row = None
                    for idx, row in enumerate(table[:5]):
                        if (
                            "Quantity" in str(row)
                            and "Item Code" in str(row)
                            and "Description" in str(row)
                        ):
                            header_row = idx
                            break

                    if header_row is None:
                        continue

                    for row in table[header_row + 1 :]:
                        if not row or len(row) < 4:
                            continue

                        try:
                            qty_str = str(row[0]).strip()
                            item_code_cell = str(row[1]).strip() if len(row) > 1 else ""
                            description = str(row[3]).strip() if len(row) > 3 else ""
                            price_str = str(row[8]).strip() if len(row) > 8 else ""
                            amount_str = str(row[11]).strip() if len(row) > 11 else ""

                            # Remove newlines from description (PDF table cells contain literal \n)
                            description = description.replace("\n", " ").replace(
                                "\r", " "
                            )
                            # Clean up multiple spaces
                            description = re.sub(r"\s+", " ", description).strip()

                            if not qty_str.isdigit():
                                continue

                            qty = int(qty_str)
                            if qty == 0 and not include_zero_qty:
                                continue

                            # Extract item number from description
                            item_code = ""
                            desc_match = re.match(r"^(\d+)\s+-\s+(.+)$", description)
                            if desc_match:
                                item_code = desc_match.group(1)
                                description = desc_match.group(2)

                            if not item_code:
                                item_code = item_code_cell

                            price = (
                                float(price_str)
                                if price_str and price_str != "None"
                                else 0.0
                            )
                            amount = (
                                float(amount_str)
                                if amount_str and amount_str != "None"
                                else 0.0
                            )

                            if item_code:
                                items.append(
                                    {
                                        "quantity": qty,
                                        "item_number": item_code,
                                        "sku": item_code,
                                        "product_description": description,
                                        "unit_price": price,
                                        "total_amount": amount,
                                    }
                                )
                        except (ValueError, IndexError):
                            continue

        return items


class Retro51Parser:
    """Parser for Retro 51 invoices - text-based."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Retro 51 invoice.

        Format: ITEM DESCRIPTION QTY RATE AMOUNT
        Example: PARR-2508K PAN AM STRATOCRUISER PLANE TORNADO RB KIT 3 37.00 111.00T
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                in_items = False

                for line in lines:
                    if "ITEM" in line and "DESCRIPTION" in line and "QTY" in line:
                        in_items = True
                        continue

                    if in_items:
                        if "SUBTOTAL" in line or "SUB TOTAL" in line:
                            break

                        match = re.match(
                            r"^([A-Z0-9\-]+)\s+(.+?)\s+(\d+)\s+([\d.]+)\s+([\d,.]+)T?$",
                            line.strip(),
                        )

                        if match:
                            item_code, description, qty_str, price_str, amount_str = (
                                match.groups()
                            )

                            try:
                                qty = int(qty_str)
                                if qty == 0 and not include_zero_qty:
                                    continue

                                items.append(
                                    {
                                        "quantity": qty,
                                        "item_number": item_code,
                                        "sku": item_code,
                                        "product_description": description.strip(),
                                        "unit_price": float(price_str),
                                        "total_amount": float(
                                            amount_str.replace(",", "")
                                        ),
                                    }
                                )
                            except ValueError:
                                continue

        return items


class TWSBIParser:
    """Parser for TWSBI invoices - text-based."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from TWSBI invoice.

        Format: SKU ACTIVITY QTY RATE AMOUNT
        Example: M7443140 Diamond 580:TWSBI Diamond 580 Clear Fountain Pen, B 1 34.00 34.00T
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                in_items = False

                for line in lines:
                    if "SKU" in line and "ACTIVITY" in line and "QTY" in line:
                        in_items = True
                        continue

                    if in_items:
                        if "SUBTOTAL" in line or "BALANCE" in line:
                            break

                        match = re.match(
                            r"^([A-Z0-9]+)\s+(.+?)\s+(\d+)\s+([\d.]+)\s+([\d.]+)T?$",
                            line.strip(),
                        )

                        if match:
                            item_code, description, qty_str, price_str, amount_str = (
                                match.groups()
                            )

                            try:
                                qty = int(qty_str)
                                if qty == 0 and not include_zero_qty:
                                    continue

                                items.append(
                                    {
                                        "quantity": qty,
                                        "item_number": item_code,
                                        "sku": item_code,
                                        "product_description": description.strip(),
                                        "unit_price": float(price_str),
                                        "total_amount": float(amount_str),
                                    }
                                )
                            except ValueError:
                                continue

        return items


class WriteUSAParser:
    """Parser for Write USA invoices - table-based format."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Write USA invoice.

        Format: Table with columns: Item | Description | Qty | Rate | Amt
        Example: VRR-2537K | TORNADO ROLLERBALL BEAUTY & THE BEAST KIT | 4 | $36.25 | $145.00
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if not tables:
                    continue

                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    # Check if this is a standard line items table (with header)
                    header = table[0]
                    has_standard_header = any(
                        "Item" in str(col) for col in header
                    ) and any("Qty" in str(col) for col in header)

                    if has_standard_header:
                        # Standard format with headers
                        # Find column indices
                        item_idx = None
                        desc_idx = None
                        qty_idx = None
                        rate_idx = None
                        amt_idx = None

                        for i, col in enumerate(header):
                            col_str = str(col).lower() if col else ""
                            if "item" in col_str and item_idx is None:
                                item_idx = i
                            elif "description" in col_str and desc_idx is None:
                                desc_idx = i
                            elif "qty" in col_str and qty_idx is None:
                                qty_idx = i
                            elif "rate" in col_str and rate_idx is None:
                                rate_idx = i
                            elif "amt" in col_str and amt_idx is None:
                                amt_idx = i

                        if None in [item_idx, desc_idx, qty_idx, rate_idx, amt_idx]:
                            continue

                        # Parse data rows
                        for row in table[1:]:
                            if not row or item_idx is None or not row[item_idx]:
                                continue

                            try:
                                item_code = (
                                    str(row[item_idx]).strip()
                                    if item_idx is not None
                                    else ""
                                )
                                description = (
                                    str(row[desc_idx])
                                    if (desc_idx is not None and row[desc_idx])
                                    else ""
                                )
                                # Remove embedded newlines
                                description = description.replace("\n", " ").replace(
                                    "\r", " "
                                )
                                description = " ".join(description.split()).strip()

                                qty_str = (
                                    str(row[qty_idx]).strip()
                                    if (qty_idx is not None and row[qty_idx])
                                    else "0"
                                )
                                qty = int(qty_str)

                                if qty == 0 and not include_zero_qty:
                                    continue

                                rate_str = (
                                    str(row[rate_idx]).strip()
                                    if (rate_idx is not None and row[rate_idx])
                                    else "0"
                                )
                                rate_str = rate_str.replace("$", "").replace(",", "")
                                unit_price = float(rate_str)

                                amt_str = (
                                    str(row[amt_idx]).strip()
                                    if (amt_idx is not None and row[amt_idx])
                                    else "0"
                                )
                                amt_str = amt_str.replace("$", "").replace(",", "")
                                total_amount = float(amt_str)

                                items.append(
                                    {
                                        "quantity": qty,
                                        "item_number": item_code,
                                        "sku": item_code,
                                        "product_description": description,
                                        "unit_price": unit_price,
                                        "total_amount": total_amount,
                                    }
                                )
                            except (ValueError, IndexError):
                                continue
                    else:
                        # Continuation page without header - assume format is: [Item, Description, Qty, Rate, Amt]
                        # Skip if first row contains None values or "SUBTOTAL"
                        if (
                            not table[0]
                            or not table[0][0]
                            or "SUBTOTAL" in str(table[0])
                        ):
                            continue

                        for row in table:
                            if not row or len(row) < 5:
                                continue
                            if (
                                not row[0]
                                or "SUBTOTAL" in str(row[0])
                                or "SHIPPING" in str(row[0])
                                or "TAX" in str(row[0])
                                or "TOTAL" in str(row[0])
                            ):
                                break

                            try:
                                item_code = str(row[0]).strip()
                                description = str(row[1]) if row[1] else ""
                                # Remove embedded newlines
                                description = description.replace("\n", " ").replace(
                                    "\r", " "
                                )
                                description = " ".join(description.split()).strip()

                                qty_str = str(row[2]).strip() if row[2] else "0"
                                qty = int(qty_str)

                                if qty == 0 and not include_zero_qty:
                                    continue

                                rate_str = str(row[3]).strip() if row[3] else "0"
                                rate_str = rate_str.replace("$", "").replace(",", "")
                                unit_price = float(rate_str)

                                amt_str = str(row[4]).strip() if row[4] else "0"
                                amt_str = amt_str.replace("$", "").replace(",", "")
                                total_amount = float(amt_str)

                                items.append(
                                    {
                                        "quantity": qty,
                                        "item_number": item_code,
                                        "sku": item_code,
                                        "product_description": description,
                                        "unit_price": unit_price,
                                        "total_amount": total_amount,
                                    }
                                )
                            except (ValueError, IndexError):
                                continue

        return items


class KenroParser:
    """Parser for Kenro invoices - table-based with multi-line descriptions."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Kenro invoice.

        Challenge:
        1. PDF contains spaced characters like "E B I R D B U . . ." that need filtering
        2. Descriptions span 2 rows:
           - Row 1: Main item with SKU, description, quantities, prices
           - Row 2: Additional detail (nib size, color, etc.) in description column only
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()

                for table in tables:
                    if not table or len(table) < 3:
                        continue

                    header_row = None
                    for idx, row in enumerate(table[:5]):
                        if "Item Code" in str(row) and "Description" in str(row):
                            header_row = idx
                            break

                    if header_row is None:
                        continue

                    # Process rows with index tracking
                    i = header_row + 1
                    while i < len(table):
                        row = table[i]

                        if not row or len(row) < 4:
                            i += 1
                            continue

                        try:
                            item_code = str(row[0]).strip() if row[0] else ""
                            description = (
                                str(row[1]).strip() if len(row) > 1 and row[1] else ""
                            )
                            qty_str = (
                                str(row[3]).strip() if len(row) > 3 and row[3] else ""
                            )
                            price_str = (
                                str(row[8]).strip() if len(row) > 8 and row[8] else ""
                            )
                            backorder_str = (
                                str(row[9]).strip() if len(row) > 9 and row[9] else ""
                            )
                            amount_str = (
                                str(row[11]).strip()
                                if len(row) > 11 and row[11]
                                else ""
                            )

                            # De-space spaced text (e.g., "E s t e r b r o o k" -> "Esterbrook")
                            # PDF rendering issue: each character is followed by a space
                            def despace_sku(text):
                                """Remove all spaces from SKU."""
                                return re.sub(r"\s+", "", text)

                            def despace_and_format_desc(text):
                                """Remove all spaces, then add spaces before capitals and special chars."""
                                # Remove all spaces
                                text = re.sub(r"\s+", "", text)
                                # Add space before capital letters (except at start and after non-letter)
                                text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
                                # Add spaces around slashes
                                text = re.sub(r"/", " / ", text)
                                # Clean up multiple spaces
                                text = re.sub(r"\s+", " ", text)
                                return text.strip()

                            # Detect spaced pattern: 3+ consecutive single-char + space sequences
                            is_spaced_item_code = re.search(r"(?:\w\s){3,}", item_code)
                            is_spaced_desc = re.search(r"(?:\w\s){3,}", description)
                            is_spaced_qty = re.search(r"\d\s+\d", qty_str)
                            
                            # De-space if needed
                            if is_spaced_item_code:
                                item_code = despace_sku(item_code)
                            if is_spaced_desc:
                                description = despace_and_format_desc(description)
                            if is_spaced_qty:
                                qty_str = re.sub(r"\s+", "", qty_str)

                            # De-space spaced numbers in prices
                            if re.search(r"\d\s+\d", price_str):
                                price_str = re.sub(r"\s+", "", price_str)
                            if re.search(r"\d\s+\d", backorder_str):
                                backorder_str = re.sub(r"\s+", "", backorder_str)
                            if re.search(r"\d\s+\d", amount_str):
                                amount_str = re.sub(r"\s+", "", amount_str)

                            # Skip rows without valid quantity
                            if not qty_str or not qty_str.isdigit():
                                i += 1
                                continue

                            # Collect multi-line description (up to 2 continuation rows)
                            continuation_rows = 0
                            for offset in [1, 2]:
                                if i + offset >= len(table):
                                    break
                                
                                next_row = table[i + offset]
                                if not next_row or len(next_row) <= 1:
                                    break
                                
                                next_item_code = str(next_row[0]).strip() if next_row[0] else ""
                                next_desc = str(next_row[1]).strip() if next_row[1] else ""
                                next_qty = str(next_row[3]).strip() if len(next_row) > 3 and next_row[3] else ""
                                
                                # If next row has an item code or quantity, it's a new item - stop
                                if next_item_code or (next_qty and next_qty.isdigit()):
                                    break
                                
                                # If next row has description, it's a continuation
                                if next_desc:
                                    # De-space if needed
                                    if re.search(r"(?:\w\s){3,}", next_desc):
                                        next_desc = despace_and_format_desc(next_desc)
                                    
                                    description += " " + next_desc
                                    continuation_rows += 1

                            qty = int(qty_str)
                            if qty == 0 and not include_zero_qty:
                                i += 1 + continuation_rows
                                continue

                            price = float(price_str) if price_str else 0.0
                            backorder = int(backorder_str) if backorder_str and backorder_str.isdigit() else 0
                            amount = float(amount_str) if amount_str else 0.0

                            if item_code and description:
                                items.append(
                                    {
                                        "quantity": qty,
                                        "backorder": backorder,
                                        "item_number": item_code,
                                        "sku": item_code,
                                        "product_description": description,
                                        "unit_price": price,
                                        "total_amount": amount,
                                    }
                                )
                            
                            # Skip past this item and its continuation rows
                            i += 1 + continuation_rows
                        except (ValueError, IndexError):
                            i += 1

        return items


class PlotterParser:
    """Parser for Plotter USA Wholesale invoices - text-based format."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Plotter USA invoice using text pattern matching.

        New Format (as of 2025):
        - Line 1: Description (first part) $TotalAmount
        - Line 2: Qty x SKU $UnitPrice
        - Line 3: (Size) USD

        Example:
        Blue Paper 2mm Grid Quadrant Graph 50 sheets $76.00
        20 x 89993204 $3.80
        (Bible Size) USD
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")

                i = 0
                while i < len(lines):
                    line = lines[i].strip()

                    # Skip header/footer/non-item lines
                    if (
                        not line
                        or "PLOTTER USA" in line
                        or "Invoice for" in line
                        or "ORDER #" in line
                        or line.startswith("http")
                        or "/3" in line
                        or "of 3" in line
                        or "Subtotal" in line
                        or "Shipping" in line
                        or "Taxes" in line
                        or line == "Total"
                        or "Customer information" in line
                        or "support@plotterusa.com" in line
                        or "View your order" in line
                        or "Order summary" in line
                        or "Item Details" in line
                        or "Quantity Item Description Price Total" in line
                        or "Signal Hill" in line
                        or "United States" in line
                        or re.match(r"^\$?([\d,.]+)$", line)  # Skip standalone prices
                    ):
                        i += 1
                        continue

                    # Pattern B (2 lines): All data on one line
                    # Line 1: Qty x SKU Description (Size) $UnitPrice
                    # Line 2: USD
                    if i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        # Match: Qty x SKU Description (optional size in parens) $Price
                        matchB = re.match(
                            r"^(\d+)\s+x\s+([A-Z0-9]+)\s+(.+?)\s+\$?([\d,.]+)$", line
                        )
                        
                        if matchB and next_line == "USD":
                            qty_str = matchB.group(1)
                            item_number = matchB.group(2)
                            description = matchB.group(3).strip()
                            unit_price_str = matchB.group(4)

                            # Extract PLT code from description if present
                            plt_match = re.search(r'(PLT\d+)', description)
                            sku = plt_match.group(1) if plt_match else item_number

                            try:
                                qty = int(qty_str)
                                if qty == 0 and not include_zero_qty:
                                    i += 2
                                    continue

                                unit_price = float(unit_price_str.replace(",", ""))
                                total = unit_price * qty

                                items.append(
                                    {
                                        "quantity": qty,
                                        "item_number": item_number,
                                        "sku": sku,
                                        "product_description": description,
                                        "unit_price": unit_price,
                                        "total_amount": total,
                                    }
                                )
                                i += 2
                                continue
                            except (ValueError, IndexError):
                                pass

                    # Pattern A (3 lines):
                    # Line 1: Description $Total
                    # Line 2: Qty x SKU $UnitPrice
                    # Line 3: (Size) USD
                    if i + 2 < len(lines):
                        line1 = line
                        line2 = lines[i + 1].strip()
                        line3 = lines[i + 2].strip()

                        # Match line 1: Description ending with $amount
                        match1 = re.match(r"^(.+?)\s+\$?([\d,.]+)$", line1)
                        # Match line 2: Qty x SKU $UnitPrice
                        match2 = re.match(
                            r"^(\d+)\s+x\s+([A-Z0-9]+)\s+\$?([\d,.]+)$", line2
                        )
                        # Match line 3: (Size) USD or just USD
                        match3 = re.match(r"^\(([^)]+)\)\s+USD$", line3) or (
                            line3 == "USD" and True
                        )

                        if match1 and match2 and match3:
                            description = match1.group(1).strip()
                            total_str = match1.group(2)
                            qty_str = match2.group(1)
                            item_number = match2.group(2)
                            unit_price_str = match2.group(3)

                            # Add size to description if present
                            if isinstance(match3, re.Match):
                                size = match3.group(1)
                                description = f"{description} ({size})"

                            # Extract PLT code from description if present
                            plt_match = re.search(r'(PLT\d+)', description)
                            sku = plt_match.group(1) if plt_match else item_number

                            try:
                                qty = int(qty_str)
                                if qty == 0 and not include_zero_qty:
                                    i += 3
                                    continue

                                unit_price = float(unit_price_str.replace(",", ""))
                                total = float(total_str.replace(",", ""))

                                items.append(
                                    {
                                        "quantity": qty,
                                        "item_number": item_number,
                                        "sku": sku,
                                        "product_description": description,
                                        "unit_price": unit_price,
                                        "total_amount": total,
                                    }
                                )
                                i += 3
                                continue
                            except (ValueError, IndexError):
                                pass

                    i += 1

        return items


class TSLParser:
    """Parser for TSL (The Superior Labor) invoices - text-based format with optional color lines."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from TSL invoice using text pattern matching.

        Two formats:
        Format 1 (no color): SKU Description × Qty
        Example: SL_0707w00 Heart concho D × 3

        Format 2 (with color): SKU Description × Qty
                               Color
        Example: SL_0370w00 TSL_crew Patch × 12
                 Red

        Color lines are lowercase or title case and don't start with SKU pattern.
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                i = 0

                while i < len(lines):
                    line = lines[i].strip()

                    # Skip header/footer/non-item lines
                    if (
                        not line
                        or "ORDER NUMBER" in line
                        or "of 6" in line
                        or "Items being shipped" in line
                        or "Your Order" in line
                        or "tracking number" in line
                        or "View Orders" in line
                        or "service@nap-dog.com" in line
                        or line == "または"
                        or line.startswith("UPS tracking")
                    ):
                        i += 1
                        continue

                    # Pattern: SKU Description × Qty
                    # SKU format: SL_####w## or similar
                    pattern = r"^(SL_\w+)\s+(.+?)\s+×\s+(\d+)$"
                    match = re.match(pattern, line)

                    if match:
                        sku, description, qty_str = match.groups()

                        qty = int(qty_str)
                        if qty == 0 and not include_zero_qty:
                            i += 1
                            continue

                        # Check if next line is a color (lowercase/titlecase, not a SKU line)
                        color = None
                        if i + 1 < len(lines):
                            next_line = lines[i + 1].strip()
                            # Color line: not empty, not a SKU pattern, not a footer
                            if (
                                next_line
                                and not re.match(r"^SL_", next_line)
                                and "of 6" not in next_line
                                and "ORDER NUMBER" not in next_line
                            ):
                                color = next_line
                                i += 1  # Skip the color line

                        # Build description with color if present
                        if color:
                            full_description = f"{description.strip()} ({color})"
                        else:
                            full_description = description.strip()

                        # No pricing info in TSL invoices, so set to 0
                        items.append(
                            {
                                "quantity": qty,
                                "item_number": sku,
                                "sku": sku,
                                "product_description": full_description,
                                "unit_price": 0.0,
                                "total_amount": 0.0,
                            }
                        )

                    i += 1

        return items


class AvantiParser:
    """Parser for Avanti Press invoices - text-based format with line items."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Avanti Press invoice using text pattern matching.

        Format: #<Ln#> <Prod#> <Description> <Price> <Shipped> <Line Amount>
        Example: #1 NB2014 QUILLED BIRTHDAY QUEEN 7.475 3.0 22.43

        Items start with # followed by line number, then product code, description,
        price, quantity shipped, and line amount.
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")

                # Pattern to match line items:
                # #<num> <prod_code> <description...> <price> <qty> <amount>
                # The description can be multiple words
                pattern = (
                    r"^#(\d+)\s+(\S+)\s+(.+?)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)$"
                )

                for line in lines:
                    match = re.match(pattern, line.strip())
                    if match:
                        (
                            line_num,
                            prod_code,
                            description,
                            price_str,
                            qty_str,
                            amount_str,
                        ) = match.groups()

                        # Parse numeric values
                        qty = float(qty_str)
                        if qty == 0 and not include_zero_qty:
                            continue

                        price = float(price_str)
                        amount = float(amount_str)

                        # Clean up description
                        description = description.strip()

                        items.append(
                            {
                                "quantity": int(qty) if qty == int(qty) else qty,
                                "item_number": prod_code,
                                "sku": prod_code,
                                "product_description": description,
                                "unit_price": price,
                                "total_amount": amount,
                            }
                        )

        return items


class ExaclairParser:
    """Parser for Exaclair invoices - text-based single-line format."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Exaclair invoice.

        Format: Ordered Shipped B.Order ItemNumber Description Price Disc% TotalPrice
        Example: 10 10 0 68145C CLASSIC NBK WB R/MAR 50S 8X11 10.45 50.00 52.25

        Pattern: Ordered(int) Shipped(int) Backordered(int) ItemNum(str) Description(str) Price(float) Disc%(float) Total(float)
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                in_items = False

                for line in lines:
                    # Detect start of items section
                    if (
                        "Item Number Description" in line
                        or "Ordered Shipped B. Order" in line
                    ):
                        in_items = True
                        continue

                    # Exit items section on totals/footer
                    if in_items and (
                        "Subtotal" in line
                        or "INVOICE Page" in line
                        or "Comments:" in line
                    ):
                        in_items = False
                        continue

                    if in_items and line.strip():
                        # Pattern: ordered shipped backordered item# description price disc% total
                        # Example: 10 10 0 68145C CLASSIC NBK WB R/MAR 50S 8X11 10.45 50.00 52.25
                        match = re.match(
                            r"^(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(.+?)\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)$",
                            line.strip(),
                        )

                        if match:
                            (
                                ordered_str,
                                shipped_str,
                                backordered_str,
                                item_num,
                                description,
                                price_str,
                                disc_str,
                                total_str,
                            ) = match.groups()

                            try:
                                # Use shipped quantity (actual quantity received)
                                qty = int(shipped_str)

                                if qty == 0 and not include_zero_qty:
                                    continue

                                retail_price = Decimal(price_str.replace(",", ""))
                                discount_pct = Decimal(disc_str.replace(",", ""))
                                # Calculate unit price: Retail * (1 - Discount / 100)
                                # Example: 35.20 * (1 - 52.5 / 100) = 16.72
                                unit_price_dec = retail_price * (
                                    1 - (discount_pct / Decimal("100.0"))
                                )
                                unit_price = float(
                                    unit_price_dec.quantize(
                                        Decimal("0.01"), rounding=ROUND_HALF_UP
                                    )
                                )

                                items.append(
                                    {
                                        "quantity": qty,
                                        "item_number": item_num.strip(),
                                        "sku": item_num.strip(),
                                        "product_description": description.strip(),
                                        "unit_price": unit_price,
                                        "total_amount": float(
                                            total_str.replace(",", "")
                                        ),
                                    }
                                )
                            except ValueError:
                                continue

        return items


class UniBallParser:
    """Parser for Uni-Ball invoices - multi-line format with repeating headers."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Uni-Ball invoice.

        Format: Each item has repeating header followed by data line and description continuation.
        Header: Line Product ID Description Quantity Net Price Net Value
               Customer Part ID
        Data: LineNum ProductID Description Qty Price Total
        Continuation: description detail

        Example:
        Line Product ID Description Quantity Net Price Net Value
        Customer Part ID
        10 4041268 safari hp fntn pen giftset 3 Each 74.25 USD / 1 Each 222.75USD
        ex-fine
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                i = 0

                while i < len(lines):
                    line = lines[i].strip()

                    # Look for repeating header pattern
                    if (
                        "Line Product ID" in line
                        and "Description" in line
                        and "Quantity" in line
                    ):
                        # Skip header line and "Customer Part ID" line
                        i += 2
                        if i >= len(lines):
                            break

                        # Next line should be the data line
                        data_line = lines[i].strip()

                        # Pattern: LineNum ProductID Description Qty Price Total
                        # Example: 10 4041268 safari hp fntn pen giftset 3 Each 74.25 USD / 1 Each 222.75USD
                        match = re.match(
                            r"^(\d+)\s+(\d+)\s+(.+?)\s+(\d+)\s+Each\s+([\d,.]+)\s+USD\s+/\s+\d+\s+Each\s+([\d,.]+)USD$",
                            data_line,
                        )

                        if match:
                            (
                                line_num,
                                product_id,
                                description,
                                qty_str,
                                price_str,
                                total_str,
                            ) = match.groups()

                            # Get continuation line (description detail)
                            i += 1
                            continuation = ""
                            if i < len(lines):
                                next_line = lines[i].strip()
                                # Check if it's a continuation (not a new header or data line)
                                if (
                                    next_line
                                    and not next_line.startswith("Line Product ID")
                                    and not re.match(r"^\d+\s+\d+\s+", next_line)
                                ):
                                    continuation = next_line

                            # Combine description parts
                            full_description = (
                                f"{description.strip()} {continuation}".strip()
                            )

                            try:
                                qty = int(qty_str)

                                if qty == 0 and not include_zero_qty:
                                    i += 1
                                    continue

                                items.append(
                                    {
                                        "quantity": qty,
                                        "item_number": product_id.strip(),
                                        "sku": product_id.strip(),
                                        "product_description": full_description,
                                        "unit_price": float(price_str.replace(",", "")),
                                        "total_amount": float(
                                            total_str.replace(",", "")
                                        ),
                                    }
                                )
                            except ValueError:
                                pass

                    i += 1

        return items


class AmeicoParser:
    """Parser for Ameico invoices - multi-line descriptions with data on first line."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Ameico invoice.

        Format:
        - Header: Item Cust. SKU Description Ordered Rate Amount
        - Line 1: ITEM_CODE Description_Part1 QUANTITY $RATE $AMOUNT
        - Line 2-N: Description continuation (multi-line)

        Challenge: Descriptions span multiple lines, need to identify where
        next item starts by detecting the item code pattern.
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                in_items = False
                i = 0

                while i < len(lines):
                    line = lines[i]

                    # Detect header
                    if "Item" in line and "Description" in line and "Ordered" in line:
                        in_items = True
                        i += 1
                        continue

                    if in_items:
                        # Stop at subtotal
                        if "Subtotal" in line or "Tax Total" in line:
                            break

                        # Pattern: ITEM_CODE Description QUANTITY $RATE $AMOUNT
                        # Item codes contain letters, numbers, hyphens, and parentheses
                        # Example: TO-T152OR(AI) Toyo - Steel Stackable 6 $16.00 $96.00
                        # Description can contain hyphens with spaces: "Karst - A5 Softcover"
                        # Match everything up to the last occurrence of: DIGIT $AMOUNT $AMOUNT
                        match = re.search(
                            r"^([A-Za-z0-9\-()]+)\s+(.*?)\s+(\d+)\s+\$([\d,.]+)\s+\$([\d,.]+)$",
                            line.strip(),
                        )

                        if match:
                            item_code, desc_start, qty_str, price_str, amount_str = (
                                match.groups()
                            )

                            try:
                                qty = int(qty_str)
                                if qty == 0 and not include_zero_qty:
                                    i += 1
                                    # Skip continuation lines until next item
                                    while i < len(lines):
                                        next_match = re.match(
                                            r"^([A-Za-z0-9\-()]+)\s+(.+?)\s+(\d+)\s+\$?([\d,.]+)\s+\$?([\d,.]+)$",
                                            lines[i].strip(),
                                        )
                                        if next_match:
                                            break
                                        i += 1
                                    continue

                                # Collect full description from continuation lines
                                description_parts = [desc_start.strip()]
                                continuation_lines = []
                                i += 1

                                # Continue reading lines until we hit the next item
                                while i < len(lines):
                                    next_line = lines[i].strip()

                                    # Check if this is the start of a new item
                                    next_match = re.match(
                                        r"^([A-Za-z0-9\-()]+)\s+(.+?)\s+(\d+)\s+\$?([\d,.]+)\s+\$?([\d,.]+)$",
                                        next_line,
                                    )

                                    if next_match:
                                        # This is a new item, stop collecting description
                                        break

                                    # Check for stop markers
                                    if (
                                        "Subtotal" in next_line
                                        or "Tax Total" in next_line
                                    ):
                                        break

                                    # Add continuation line
                                    if next_line:
                                        continuation_lines.append(next_line)
                                    i += 1

                                # If item code ends with hyphen, the LAST continuation line is likely the suffix
                                # Example: "KT-Sketchpad-A3-" with continuation lines ["Sketchpad", "Black"]
                                # "Black" is the suffix, "Sketchpad" is description
                                if item_code.endswith("-") and continuation_lines:
                                    # The suffix should be a short word at the END (like color/variant)
                                    last_line = continuation_lines[-1].strip()
                                    words = last_line.split()
                                    
                                    # Check if last line looks like an item code suffix:
                                    # - Short (1-2 words)
                                    # - Not a brand name or long description phrase
                                    # - Common suffix patterns: colors, sizes, variants
                                    if (
                                        len(words) <= 2
                                        and not last_line.startswith("Karst")
                                        and not last_line.startswith("Toyo")
                                        and not last_line.startswith("The ")
                                        and not last_line.startswith("with ")
                                        and len(last_line) < 20  # Suffixes are typically short
                                    ):
                                        # Complete the item code with the suffix
                                        item_code = item_code + last_line
                                        # Remove the suffix from description (last element)
                                        continuation_lines = continuation_lines[:-1]

                                # Join all description parts
                                description_parts.extend(continuation_lines)
                                full_description = " ".join(description_parts)

                                items.append(
                                    {
                                        "quantity": qty,
                                        "item_number": item_code,
                                        "sku": item_code,
                                        "product_description": full_description,
                                        "unit_price": float(price_str.replace(",", "")),
                                        "total_amount": float(
                                            amount_str.replace(",", "")
                                        ),
                                    }
                                )
                                # Don't increment i here - the inner while loop already did it
                                continue
                            except ValueError:
                                pass

                    i += 1

        return items


class ChartpakParser:
    """Parser for Chartpak invoices - text-based format with optional backorder column."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Chartpak invoice.

        Format: Two variations based on stock availability
        - Format 1 (items in stock): QTY EA [BO] ITEM DESC LIST DISCOUNT NET AMOUNT
        - Format 2 (backorder only): EA QTY ITEM DESC LIST DISCOUNT NET AMOUNT

        Examples:
        - "20 EA N104 B5 MM DOT 12.400 .550 5.580 111.60" (20 shipped, 0 backorder)
        - "15 EA 5 N195A A5 NOTEBOO 10.900 .550 4.905 73.58" (15 shipped, 5 backorder)
        - "EA 2 N868-51 A6 5MM GRE 32.000 .550 14.400 .00" (0 shipped, 2 backorder)
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                in_items = False

                for line in lines:
                    # Detect start of items section (after header)
                    if "SHIPPED" in line and "UNIT" in line and "CATALOG" in line:
                        in_items = True
                        continue

                    # Exit items section on subtotal
                    if "SUB-TOTAL" in line or "TOTAL" in line:
                        in_items = False
                        continue

                    if in_items and line.strip():
                        # Try Format 1: QTY EA [BO] ITEM DESC PRICES
                        match = re.match(
                            r"^(\d+)\s+EA\s+(?:(\d+)\s+)?([A-Z0-9-]+)\s+(.+?)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$",
                            line.strip(),
                        )

                        if match:
                            (
                                qty_str,
                                bo_str,
                                item_code,
                                desc,
                                list_price,
                                discount,
                                net_price,
                                amount,
                            ) = match.groups()
                            shipped_qty = int(qty_str)
                            backorder_qty = int(bo_str) if bo_str else 0

                            # Skip zero shipped if not including zero qty
                            if shipped_qty == 0 and not include_zero_qty:
                                continue

                            try:
                                items.append(
                                    {
                                        "quantity": shipped_qty,
                                        "item_number": item_code,
                                        "sku": item_code,
                                        "product_description": desc.strip(),
                                        "unit_price": float(net_price),
                                        "total_amount": float(amount),
                                    }
                                )
                            except ValueError:
                                continue
                        else:
                            # Try Format 2: EA QTY ITEM DESC PRICES (backorder only)
                            match = re.match(
                                r"^EA\s+(\d+)\s+([A-Z0-9-]+)\s+(.+?)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$",
                                line.strip(),
                            )

                            if match:
                                (
                                    backorder_qty,
                                    item_code,
                                    desc,
                                    list_price,
                                    discount,
                                    net_price,
                                    amount,
                                ) = match.groups()
                                backorder_qty = int(backorder_qty)

                                try:
                                    items.append(
                                        {
                                            "quantity": 0,
                                            "item_number": item_code,
                                            "sku": item_code,
                                            "product_description": desc.strip(),
                                            "unit_price": float(net_price),
                                            "total_amount": 0.0,  # Backorder only items have 0 amount
                                        }
                                    )
                                except ValueError:
                                    continue

        return items

    @staticmethod
    def parse_with_backorders(pdf_path: str) -> tuple[List[dict], List[dict]]:
        """
        Extract line items and backorder items from Chartpak invoice.

        Returns:
            Tuple of (regular_items, backorder_items)
            Regular items: Only items that were shipped (quantity > 0)
            Backorder items: All items with backorder quantity > 0
        """
        regular_items = []
        backorder_items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                in_items = False

                for line in lines:
                    # Detect start of items section
                    if "SHIPPED" in line and "UNIT" in line and "CATALOG" in line:
                        in_items = True
                        continue

                    # Exit items section on subtotal
                    if "SUB-TOTAL" in line or "TOTAL" in line:
                        in_items = False
                        continue

                    if in_items and line.strip():
                        # Try Format 1: QTY EA [BO] ITEM DESC PRICES
                        match = re.match(
                            r"^(\d+)\s+EA\s+(?:(\d+)\s+)?([A-Z0-9-]+)\s+(.+?)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$",
                            line.strip(),
                        )

                        if match:
                            (
                                qty_str,
                                bo_str,
                                item_code,
                                desc,
                                list_price,
                                discount,
                                net_price,
                                amount,
                            ) = match.groups()
                            shipped_qty = int(qty_str)
                            backorder_qty = int(bo_str) if bo_str else 0

                            try:
                                item = {
                                    "quantity": shipped_qty,
                                    "item_number": item_code,
                                    "sku": item_code,
                                    "product_description": desc.strip(),
                                    "unit_price": float(net_price),
                                    "total_amount": float(amount),
                                    "retail_price": float(list_price),
                                }

                                # Add to regular items if shipped
                                if shipped_qty > 0:
                                    regular_items.append(item)

                                # Add to backorder items if backordered
                                if backorder_qty > 0:
                                    backorder_item = item.copy()
                                    backorder_item["backorder_quantity"] = backorder_qty
                                    backorder_items.append(backorder_item)
                            except ValueError:
                                continue
                        else:
                            # Try Format 2: EA QTY ITEM DESC PRICES (backorder only)
                            match = re.match(
                                r"^EA\s+(\d+)\s+([A-Z0-9-]+)\s+(.+?)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$",
                                line.strip(),
                            )

                            if match:
                                (
                                    backorder_qty,
                                    item_code,
                                    desc,
                                    list_price,
                                    discount,
                                    net_price,
                                    amount,
                                ) = match.groups()
                                backorder_qty = int(backorder_qty)

                                try:
                                    backorder_item = {
                                        "quantity": 0,
                                        "item_number": item_code,
                                        "sku": item_code,
                                        "product_description": desc.strip(),
                                        "unit_price": float(net_price),
                                        "total_amount": 0.0,
                                        "retail_price": float(list_price),
                                        "backorder_quantity": backorder_qty,
                                    }
                                    backorder_items.append(backorder_item)
                                except ValueError:
                                    continue

        return regular_items, backorder_items


class JPTParser:
    """Parser for JPT invoices - text-based format with backorder column."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from JPT invoice.

        Format: SHIP ITEM# UPC SKU [P65] DESC B/O PRICE AMOUNT
        Where B/O is a numeric column marking backorder quantities.

        The B/O column contains numeric values (backorder quantities)
        or the last word of the description if there's no backorder.
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                in_items = False
                skip_next = False

                for i, line in enumerate(lines):
                    if skip_next:
                        skip_next = False
                        continue

                    # Find the header
                    if "Ship Item#" in line and "B/O" in line:
                        in_items = True
                        continue

                    # Exit on totals or footer
                    if in_items and (
                        "Subtotal" in line
                        or "TOTAL" in line
                        or line.startswith("Page")
                        or "Total:" in line
                    ):
                        in_items = False
                        continue

                    if in_items and line.strip() and not line.startswith("Page"):
                        # Parse the line
                        try:
                            item = JPTParser._parse_line(line.strip())
                            if item:
                                # Filter zero quantities if needed
                                if item["quantity"] == 0 and not include_zero_qty:
                                    continue
                                items.append(item)
                        except Exception:
                            # Skip unparseable lines
                            continue

        return items

    @staticmethod
    def _parse_line(line: str) -> Optional[dict]:
        """
        Parse a single line item from JPT invoice.

        Format: [SHIP] ITEM# UPC SKU [P65] DESC... BO PRICE AMOUNT
        Where BO is numeric and precedes PRICE and AMOUNT.
        """
        parts = line.split()

        if (
            len(parts) < 4
        ):  # Minimum: item# upc sku price amount (shipped often missing)
            return None

        try:
            # Work backwards: last 2 parts are definitely PRICE and AMOUNT
            amount_str = parts[-1]
            price_str = parts[-2]
            bo_str = parts[-3]

            # Validate PRICE and AMOUNT are floats
            amount = float(amount_str)
            price = float(price_str)

            # Try to parse B/O as numeric
            try:
                bo_qty = int(bo_str)
            except ValueError:
                # If not numeric, B/O is part of description, so actual B/O is 0
                bo_qty = 0
                # Adjust parts to not include it in the BO position
                parts = parts[:-2]  # Remove price and amount
                # Recalculate which parts are what

            # Check if first element is a small number (shipped qty) or large (item code)
            first_val = int(parts[0]) if parts[0].isdigit() else -1

            if 0 < first_val < 1000:
                # Has shipped quantity
                shipped_qty = first_val
                item_id = parts[1]
                upc = parts[2]
                sku = parts[3]
                desc_start_idx = 4
            else:
                # No shipped quantity (backorder only)
                shipped_qty = 0
                item_id = parts[0]
                upc = parts[1]
                sku = parts[2]
                desc_start_idx = 3

            # Skip P65 flag if present (single Y or N)
            if desc_start_idx < len(parts) and parts[desc_start_idx] in ("Y", "N"):
                desc_start_idx += 1

            # Description is from desc_start to B/O
            # If B/O is numeric, it's at position -3 from end (after removing prices)
            if bo_qty > 0:
                desc_end_idx = len(parts) - 3
            else:
                # B/O was part of description, so everything except last 2 (prices)
                desc_end_idx = len(parts) - 2

            if desc_start_idx < desc_end_idx:
                description = " ".join(parts[desc_start_idx:desc_end_idx])
            else:
                description = ""

            return {
                "quantity": shipped_qty,
                "item_number": item_id,
                "sku": sku,
                "product_description": description,
                "unit_price": price,
                "total_amount": amount,
            }
        except (ValueError, IndexError):
            return None

    @staticmethod
    def parse_with_backorders(pdf_path: str) -> tuple[List[dict], List[dict]]:
        """
        Extract line items and backorder items from JPT invoice.

        Returns:
            Tuple of (regular_items, backorder_items)
            Regular items: Items that were shipped (quantity > 0 and amount > 0)
            Backorder items: Items with any backorder quantity marking
        """
        all_items = JPTParser.parse(pdf_path, include_zero_qty=True)
        regular_items = []
        backorder_items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split("\n")
                in_items = False

                for i, line in enumerate(lines):
                    # Find the header
                    if "Ship Item#" in line and "B/O" in line:
                        in_items = True
                        continue

                    # Exit on totals or footer
                    if in_items and (
                        "Subtotal" in line
                        or "TOTAL" in line
                        or line.startswith("Page")
                        or "Total:" in line
                    ):
                        in_items = False
                        continue

                    if in_items and line.strip() and not line.startswith("Page"):
                        try:
                            # Check if line has backorder marker
                            parts = line.split()
                            if len(parts) < 4:
                                continue

                            # Get BO value
                            bo_str = parts[-3]
                            try:
                                bo_qty = int(bo_str)
                                has_backorder = bo_qty > 0
                            except ValueError:
                                has_backorder = False

                            item = JPTParser._parse_line(line.strip())
                            if item:
                                if item["quantity"] > 0 and item["total_amount"] > 0:
                                    regular_items.append(item)

                                if has_backorder:
                                    backorder_item = item.copy()
                                    backorder_item["backorder_quantity"] = int(bo_str)
                                    backorder_items.append(backorder_item)
                        except Exception:
                            continue

        return regular_items, backorder_items


class WearingeulParser:
    """Parser for Wearingeul (Abledesign Entertainment) order sheets - table-based format."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Wearingeul order sheet PDF.

        Format: Table with columns like:
        - Ink: NO. | Set Name | Option | Ink Code | GTIN-13 | Supply Cost | Order Quantity | Total
        - Note & Paper: No | Paper | Title | Option | Size | GTIN-13 | List Price | Supply Cost | Order Quantity | Total
        - ETC: No | Title | Option | Size | GTIN-13 | List Price | Supply Cost | Order Quantity | Total

        Set Name/Paper/Title can span multiple rows (appears once for a group, then None for subsequent items)
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if not tables:
                    continue

                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    # Find header row and determine column structure
                    header_row = None
                    data_start_idx = 0

                    for i, row in enumerate(table):
                        if row and any(
                            col
                            and (
                                "Order Quantity" in str(col)
                                or "Order Quantitiy" in str(col)
                            )
                            for col in row
                        ):
                            header_row = row
                            data_start_idx = i + 1
                            break

                    if not header_row:
                        continue

                    # Determine column indices
                    col_indices = {}
                    for i, col in enumerate(header_row):
                        if col:
                            col_lower = str(col).lower()
                            if "no" in col_lower and not col_indices.get("no"):
                                col_indices["no"] = i
                            elif (
                                "set name" in col_lower
                                or "paper" in col_lower
                                or "title" in col_lower
                            ):
                                col_indices["set_name"] = i
                            elif "option" in col_lower:
                                col_indices["option"] = i
                            elif "ink code" in col_lower or "code" in col_lower:
                                col_indices["item_code"] = i
                            elif "gtin" in col_lower:
                                col_indices["gtin"] = i
                            elif "supply cost" in col_lower:
                                col_indices["unit_price"] = i
                            elif "order quant" in col_lower:
                                col_indices["quantity"] = i
                            elif col_lower.strip() == "total":
                                col_indices["total"] = i

                    # Track last seen set name for multi-row items
                    last_set_name = ""

                    # Parse data rows
                    for row in table[data_start_idx:]:
                        if not row or not any(row):
                            continue

                        # Skip rows that look like headers or subtotals
                        if row[0] and str(row[0]).lower() in [
                            "no",
                            "no.",
                            "total",
                            "subtotal",
                        ]:
                            continue

                        try:
                            # FIRST: Update last_set_name if this row has a set name
                            # This must happen BEFORE checking quantity, so we track titles even for skipped rows
                            if col_indices.get("set_name") is not None:
                                set_name_val = row[col_indices["set_name"]]
                                if set_name_val and str(set_name_val).strip():
                                    last_set_name = str(set_name_val).strip()

                            # Get quantity
                            qty_str = (
                                str(row[col_indices["quantity"]])
                                if col_indices.get("quantity")
                                and row[col_indices["quantity"]]
                                else "0"
                            )
                            qty_str = qty_str.strip().replace("$", "").replace(")", "")

                            if not qty_str or qty_str == "":
                                quantity = 0
                            else:
                                quantity = int(float(qty_str))

                            # Skip zero quantity items if not including them
                            if quantity == 0 and not include_zero_qty:
                                continue

                            # Get item code (could be in 'item_code' column or 'option' column for some formats)
                            item_code = ""
                            if col_indices.get("item_code") is not None:
                                item_code = (
                                    str(row[col_indices["item_code"]])
                                    if row[col_indices["item_code"]]
                                    else ""
                                )

                            # If no item code column, try GTIN
                            if not item_code and col_indices.get("gtin") is not None:
                                item_code = (
                                    str(row[col_indices["gtin"]])
                                    if row[col_indices["gtin"]]
                                    else ""
                                )

                            item_code = item_code.strip().replace(")", "")

                            # Get option/description
                            option = ""
                            if col_indices.get("option") is not None:
                                option = (
                                    str(row[col_indices["option"]])
                                    if row[col_indices["option"]]
                                    else ""
                                )
                                option = option.strip()

                            # Build description from set_name + option
                            if last_set_name and option:
                                description = f"{last_set_name} - {option}"
                            elif last_set_name:
                                description = last_set_name
                            elif option:
                                description = option
                            else:
                                description = ""

                            # Remove embedded newlines and extra whitespace from description
                            description = description.replace("\n", " ").replace(
                                "\r", " "
                            )
                            description = " ".join(
                                description.split()
                            )  # Normalize whitespace

                            # Get unit price
                            price_str = (
                                str(row[col_indices["unit_price"]])
                                if col_indices.get("unit_price")
                                and row[col_indices["unit_price"]]
                                else "0"
                            )
                            price_str = (
                                price_str.strip().replace("$", "").replace(")", "")
                            )
                            unit_price = float(price_str) if price_str else 0.0

                            # Get total
                            total_str = (
                                str(row[col_indices["total"]])
                                if col_indices.get("total")
                                and row[col_indices["total"]]
                                else "0"
                            )
                            total_str = (
                                total_str.strip().replace("$", "").replace(")", "")
                            )
                            total_amount = float(total_str) if total_str else 0.0

                            if item_code:  # Only add items with an item code
                                items.append(
                                    {
                                        "quantity": quantity,
                                        "item_number": item_code,
                                        "sku": item_code,
                                        "product_description": description,
                                        "unit_price": unit_price,
                                        "total_amount": total_amount,
                                    }
                                )
                        except (ValueError, IndexError, KeyError):
                            continue

        return items


class EliteAccessoriesParser:
    """Parser for Elite Accessories invoices - handles split columns across pages."""

    @staticmethod
    def parse(pdf_path: str, include_zero_qty: bool = True) -> List[dict]:
        """
        Extract line items from Elite Accessories invoice.

        Format: QTY BO ITEMNO DESCRIPTION SIZE UNITPRICE LINETOTAL
        Challenge: LINETOTAL column wraps to next page due to Excel export width
        Invoice starts on page 3 (index 2)
        
        Strategy:
        1. Use table extraction to preserve column structure
        2. Match tables across pages by looking for header patterns
        3. Combine data from split tables
        """
        items = []

        with pdfplumber.open(pdf_path) as pdf:
            # Skip to page 3 (index 2) where invoice starts
            if len(pdf.pages) < 3:
                return items

            print(f"[EliteAccessories] Total pages: {len(pdf.pages)}")
            
            # Extract tables from all invoice pages
            for page_num in range(2, len(pdf.pages)):
                page = pdf.pages[page_num]
                
                # Use table extraction with custom settings
                tables = page.extract_tables(table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "explicit_vertical_lines": [],
                    "explicit_horizontal_lines": [],
                    "snap_tolerance": 3,
                    "join_tolerance": 3,
                    "edge_min_length": 3,
                    "min_words_vertical": 3,
                    "min_words_horizontal": 1,
                })
                
                if not tables:
                    print(f"[EliteAccessories] Page {page_num + 1}: No tables found")
                    continue
                
                for table_idx, table in enumerate(tables):
                    if not table or len(table) < 2:
                        continue
                    
                    # Check if this is the items table by looking for header
                    header_row = table[0]
                    header_str = ' '.join([str(cell or '').strip() for cell in header_row]).lower()
                    
                    if 'qty' not in header_str and 'item no' not in header_str:
                        continue
                    
                    print(f"[EliteAccessories] Page {page_num + 1}, Table {table_idx}: {len(table)} rows, {len(header_row)} columns")
                    print(f"  Header: {header_row}")
                    
                    # Find column indices
                    qty_idx = None
                    bo_idx = None
                    item_idx = None
                    desc_idx = None
                    size_idx = None
                    price_idx = None
                    
                    for i, cell in enumerate(header_row):
                        cell_str = str(cell or '').strip().lower()
                        if 'qty' in cell_str:
                            qty_idx = i
                        elif 'bo' in cell_str or 'backorder' in cell_str:
                            bo_idx = i
                        elif 'item' in cell_str and 'no' in cell_str:
                            item_idx = i
                        elif 'description' in cell_str:
                            desc_idx = i
                        elif 'size' in cell_str:
                            size_idx = i
                        elif 'price' in cell_str and 'unit' in cell_str:
                            price_idx = i
                    
                    print(f"  Column indices: qty={qty_idx}, bo={bo_idx}, item={item_idx}, desc={desc_idx}, size={size_idx}, price={price_idx}")
                    
                    # Parse data rows
                    for row_idx, row in enumerate(table[1:], start=1):
                        if not row or len(row) == 0:
                            continue
                        
                        # Skip rows that don't have quantity
                        if qty_idx is None or qty_idx >= len(row):
                            continue
                        
                        qty_cell = str(row[qty_idx] or '').strip()
                        if not qty_cell or not qty_cell.isdigit():
                            continue
                        
                        try:
                            qty = int(qty_cell)
                            if qty == 0 and not include_zero_qty:
                                continue
                            
                            # Extract other fields
                            bo = 0
                            if bo_idx is not None and bo_idx < len(row):
                                bo_cell = str(row[bo_idx] or '').strip()
                                if bo_cell.isdigit():
                                    bo = int(bo_cell)
                            
                            item_no = ''
                            if item_idx is not None and item_idx < len(row):
                                item_no = str(row[item_idx] or '').strip()
                            
                            description = ''
                            if desc_idx is not None and desc_idx < len(row):
                                description = str(row[desc_idx] or '').strip()
                            
                            size = ''
                            if size_idx is not None and size_idx < len(row):
                                size = str(row[size_idx] or '').strip()
                            
                            unit_price = 0.0
                            if price_idx is not None and price_idx < len(row):
                                price_cell = str(row[price_idx] or '').replace('$', '').replace(',', '').strip()
                                if price_cell:
                                    unit_price = float(price_cell)
                            
                            # Build product description
                            desc_parts = []
                            if description:
                                desc_parts.append(description)
                            if size:
                                desc_parts.append(size)
                            product_desc = ' '.join(desc_parts) if desc_parts else item_no
                            
                            items.append({
                                'quantity': qty,
                                'backorder': bo,
                                'item_number': item_no,
                                'sku': item_no,
                                'product_description': product_desc,
                                'unit_price': unit_price,
                                'total_amount': round(qty * unit_price, 2)
                            })
                            
                            if len(items) <= 5:
                                print(f"[EliteAccessories] Parsed: qty={qty}, bo={bo}, item={item_no}, desc='{product_desc}', price={unit_price}")
                        
                        except (ValueError, IndexError) as e:
                            print(f"[EliteAccessories] Error parsing row {row_idx}: {e}")
                            continue

            print(f"[EliteAccessories] Extracted {len(items)} items")
            
        return items


# Parser registry mapping vendor names to parser classes
CUSTOM_PARSERS = {
    "itoya": ItoyaParser,
    "luxury_brands": LuxuryBrandsParser,
    "rediform": RediformParser,
    "toms_studio": TomsStudioParser,
    "coles": ColesParser,
    "lamy": LamyParser,
    "pilot": PilotParser,
    "montblanc": MontblancParser,
    "lighthouse": LighthouseParser,
    "retro51": Retro51Parser,
    "twsbi": TWSBIParser,
    "writeusa": WriteUSAParser,
    "kenro": KenroParser,
    "avanti": AvantiParser,
    "plotter": PlotterParser,
    "tsl": TSLParser,
    "exaclair": ExaclairParser,
    "uniball": UniBallParser,
    "ameico": AmeicoParser,
    "chartpak": ChartpakParser,
    "jpt": JPTParser,
    "wearingeul": WearingeulParser,
    "elite_accessories": EliteAccessoriesParser,
}


def detect_vendor(text: str) -> str:
    """
    Detect the invoice vendor from the first page text.

    Args:
        text: Extracted text from the first page of the invoice

    Returns:
        Vendor identifier string, or empty string if no match
    """
    if "ITOYA" in text and "Item Description UOM" in text:
        return "itoya"
    elif "Luxury Brands" in text or "luxurybrands" in text.lower():
        return "luxury_brands"
    elif "Rediform" in text or "REDIFORM" in text:
        return "rediform"
    elif "Tom's Studio" in text:
        return "toms_studio"
    elif "Coles" in text and "McAlpine Park Drive" in text:
        return "coles"
    elif "Lamy USA" in text or "LAMY" in text:
        return "lamy"
    elif "PCAINV" in text and "DALLAS, TX" in text:
        return "pilot"
    elif "Montblanc North America" in text:
        return "montblanc"
    elif "Lighthouse Publications" in text:
        return "lighthouse"
    elif "Retro 1951" in text or "retro51.com" in text:
        return "retro51"
    elif "TWSBI INC" in text:
        return "twsbi"
    elif "Write USA LLC" in text:
        return "writeusa"
    elif "Kenro Industries" in text:
        return "kenro"
    elif "AVANTI PRESS" in text:
        return "avanti"
    elif "PLOTTER USA" in text or "plotterusa.com" in text.lower():
        return "plotter"
    elif "ORDER NUMBER" in text and "SL_" in text:
        return "tsl"
    elif "EXACLAIR, INC." in text or "EXACLAIR" in text:
        return "exaclair"
    elif "uni-ball Corporation" in text or "uni-ball" in text:
        return "uniball"
    elif "Ameico" in text and "New Milford CT" in text:
        return "ameico"
    elif "CHARTPAK" in text or "CHARTPAK, INC" in text:
        return "chartpak"
    elif "JPT AMERICA" in text or "jptamerica" in text.lower():
        return "jpt"
    elif "Abledesign Entertainment" in text or "Order Sheet" in text:
        return "wearingeul"
    elif "Elite Accessories" in text or "ELITE ACCESSORIES" in text:
        return "elite_accessories"

    return ""


def parse_with_custom_parser(
    pdf_path: str, include_zero_qty: bool = True
) -> List[dict]:
    """
    Attempt to parse invoice with a custom parser based on vendor detection.

    Args:
        pdf_path: Path to the PDF invoice or Excel file
        include_zero_qty: Whether to include zero-quantity line items

    Returns:
        List of line item dictionaries, or empty list if no custom parser matches
    """

    # Read first page of PDF to detect vendor
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return []

        text = pdf.pages[0].extract_text() or ""

    # Detect vendor
    vendor = detect_vendor(text)

    if vendor and vendor in CUSTOM_PARSERS:
        parser_class = CUSTOM_PARSERS[vendor]
        print(f"Using {vendor.replace('_', ' ').title()} custom parser with new version")
        return parser_class.parse(pdf_path, include_zero_qty)

    return []
