"""Read and stage Deadlock's numeric string tables in a Unity resources file."""

from collections import Counter
from hashlib import sha256
import re
import struct

from deadlock_tools.shipdb import read_objects


LANGUAGES = {
    "en": ("English", ""), "fr": ("French", "_french"),
    "de": ("German", "_german"), "es": ("Spanish", "_spanish"),
    "ru": ("Russian", "_russian"),
}
TABLE_NAMES = (
    "default", "blueprint_descriptions", "achievements", "activity_feed", "barks",
    "dialogue", "holoinfo", "keymappings", "loading_hints", "mission",
    "mission_descriptions", "mission_titles", "options", "resource_mission_descriptions",
    "resource_mission_titles", "skirmish_mission_titles", "tutorial_cues",
)
SHIP_REFERENCES = {
    "descriptionID": ("default", "Display name"),
    "descriptionShortID": ("default", "Short name"),
    "DescriptionHeaderID": ("blueprint_descriptions", "Blueprint header"),
    "DescriptionBodyID": ("blueprint_descriptions", "Blueprint body"),
}
ROW = re.compile(r"([0-9]+)\|([^|]*)(.*)")


def read_string_rows(script):
    """Return (line index, ID, text, trailing delimiter and comment)."""
    lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|\Z)", script)
    if lines and not lines[-1]:
        lines.pop()
    entries = []
    for index, raw in enumerate(lines):
        match = ROW.fullmatch(raw.rstrip("\r\n"))
        if match:
            key, text, suffix = match.groups()
            entries.append((index, int(key), text, suffix))
    return lines, entries


class Localisation:
    def __init__(self, data):
        self.data = data
        if len(data) < 20:
            raise ValueError("Not a Deadlock resources.assets file.")
        _, file_size, version, self.offset = struct.unpack_from(">IIII", data)
        if version != 17 or file_size != len(data) or data[16] != 0:
            raise ValueError("Expected Deadlock's little-endian Unity resources.assets format (version 17).")
        self.objects = sorted(read_objects(data), key=lambda item: item[1])
        self.revision = sha256(data).hexdigest()
        self.tables = {}
        expected = {table + suffix: (table, lang) for table in TABLE_NAMES
                    for lang, (_, suffix) in LANGUAGES.items()}
        for pid, start, size, class_id, _ in self.objects:
            if class_id != 49:
                continue
            name_length = struct.unpack_from("<I", data, start)[0]
            name_start = start + 4
            script_length_at = (name_start + name_length + 3) & ~3
            if not start <= script_length_at <= start + size - 4:
                raise ValueError("Invalid resources TextAsset name length.")
            name = data[name_start:name_start + name_length].decode("utf-8")
            if name not in expected:
                continue
            table, lang = expected[name]
            script_length = struct.unpack_from("<I", data, script_length_at)[0]
            script_start = script_length_at + 4
            script_end = script_start + script_length
            if (script_end + 3) & ~3 != start + size:
                raise ValueError(f"Invalid TextAsset length for {name}.")
            variants = self.tables.setdefault(table, {})
            if lang in variants:
                raise ValueError(f"Duplicate localisation table asset {name}.")
            variants[lang] = (pid, data[start:script_length_at], data[script_start:script_end].decode("utf-8"))
        if not {"default", "blueprint_descriptions"} <= self.tables.keys():
            raise ValueError("Ship localisation tables were not found in this resources.assets.")

    def table(self, name):
        variants = self.tables.get(name)
        if not variants:
            raise ValueError("Unknown localisation table.")
        rows = {}
        for lang in LANGUAGES:
            if lang not in variants:
                continue
            _, _, script = variants[lang]
            _, entries = read_string_rows(script)
            counts = Counter(key for _, key, _, _ in entries)
            for _, key, text, suffix in entries:
                row = rows.setdefault(key, {"id": key, "translations": {}, "duplicates": []})
                row["translations"].setdefault(lang, {
                    "text": text, "note": suffix[1:] if suffix.startswith("|") else "",
                })
                if counts[key] > 1 and lang not in row["duplicates"]:
                    row["duplicates"].append(lang)
        return {
            "languages": {lang: label for lang, (label, _) in LANGUAGES.items() if lang in variants},
            "rows": [rows[key] for key in sorted(rows)],
            "next_id": max(rows, default=0) + 1,
        }

    def update(self, table, revision, key, create, translations, note=""):
        if revision != self.revision:
            raise ValueError("Localisation changed on disk. Reload the table before saving.")
        if type(key) is not int or not 1 <= key <= 2147483647:
            raise ValueError("String ID must be a positive 32-bit integer.")
        if type(create) is not bool or not isinstance(translations, dict) or not translations:
            raise ValueError("Provide at least one translation to save.")
        current = self.table(table)
        variants = self.tables[table]
        entry = next((row for row in current["rows"] if row["id"] == key), None)
        if create and entry is not None:
            raise ValueError(f"ID {key} already exists in {table}. Choose an unused ID.")
        if not create and entry is None:
            raise ValueError(f"ID {key} does not exist in {table}.")
        if any(lang not in variants for lang in translations):
            raise ValueError("Unknown language for this table.")
        if create and set(translations) != variants.keys():
            raise ValueError("Supply a translation or the English fallback for each language.")
        if not isinstance(note, str) or any(char in note for char in "\r\n\0|"):
            raise ValueError("Notes must be on one line without pipe characters.")
        replacements = {}
        for lang, text in translations.items():
            if not isinstance(text, str) or not text.strip() or any(char in text for char in "\r\n\0|"):
                raise ValueError("Translations must be non-empty and on one line. Use <br> for line breaks.")
            pid, prefix, script = variants[lang]
            lines, entries = read_string_rows(script)
            matches = [(index, suffix) for index, row_key, _, suffix in entries if row_key == key]
            if len(matches) > 1:
                raise ValueError(f"ID {key} has duplicate rows in {LANGUAGES[lang][0]}.")
            if matches:
                index, suffix = matches[0]
                row = lines[index].rstrip("\r\n")
                lines[index] = row.split("|", 1)[0] + "|" + text + suffix + lines[index][len(row):]
            else:
                newline = next(iter(re.findall(r"\r\n|\n|\r", script)), "\r\n")
                if lines and not lines[-1].endswith(("\r", "\n")):
                    lines[-1] += newline
                lines.append(f"{key}|{text}|{note}{newline}")
            encoded = "".join(lines).encode("utf-8")
            payload = prefix + struct.pack("<I", len(encoded)) + encoded
            replacements[pid] = payload + bytes((-len(payload)) % 4)
        output = self._replace_objects(replacements)
        verified = self.__class__(output)
        saved = next(row for row in verified.table(table)["rows"] if row["id"] == key)
        if any(saved["translations"].get(lang, {}).get("text") != text for lang, text in translations.items()):
            raise ValueError("The saved localisation failed its round-trip check.")
        return output

    def _replace_objects(self, replacements):
        header = bytearray(self.data[:self.offset])
        parts = []
        cursor = self.offset
        shift = 0
        for pid, start, size, _, metadata in self.objects:
            if start < cursor or start + size > len(self.data):
                raise ValueError("The resources file contains invalid object bounds.")
            struct.pack_into("<I", header, metadata + 8, start - self.offset + shift)
            if pid not in replacements:
                continue
            payload = replacements[pid]
            parts.extend((self.data[cursor:start], payload))
            struct.pack_into("<I", header, metadata + 12, len(payload))
            delta = len(payload) - size
            padding = (-delta) % 8
            parts.append(bytes(padding))
            cursor = start + size
            shift += delta + padding
        parts.append(self.data[cursor:])
        struct.pack_into(">I", header, 4, len(self.data) + shift)
        return b"".join((header, *parts))
