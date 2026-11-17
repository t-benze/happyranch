#!/usr/bin/env bash
#
# CI gate for the web design system. Runs typecheck + lint + tests +
# deterministic Storybook static build + complete production-source colour scan
# + unknown-token utility scan.
#
# Exit codes:
#   0 — clean
#   1 — typecheck, lint, test, or build failure (or missing scan input)
#   3 — production colour baseline mismatch
#   4 — a colour/spacing/radius/shadow utility names an undefined theme token
#   5 — tokens.css re-maps a numeric --spacing-N off Tailwind's linear scale
#   6 — a non-semantic (alias) --color-* in tokens.css is not a var() alias of
#       a semantic token
#
# Modes: `--scan-hex` (colour baseline only), `--scan-tokens` (tokens.css
# spacing/alias checks, then unknown-token utilities).
#
# Run locally before pushing: `bash scripts/verify-design-system.sh`
set -euo pipefail

cd "$(dirname "$0")/.."

scan_colours() {
  local source_root=${DESIGN_SYSTEM_SOURCE_ROOT:-src}
  local allowlist=${DESIGN_SYSTEM_HEX_ALLOWLIST:-scripts/design-system-hex-allowlist.tsv}

  python3 - "$source_root" "$allowlist" <<'PY'
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import sys

source_root = Path(sys.argv[1])
allowlist_path = Path(sys.argv[2])
extensions = {".css", ".js", ".jsx", ".ts", ".tsx"}
excluded_directories = {"__snapshots__", "__tests__", "reports", "screenshots", "test"}

hex_value = r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F](?:[0-9a-fA-F]{2}){0,2})?"
colour_properties = r"(?:color|background|border|fill|stroke|shadow|outline|decoration|accent|caret)"
colour_utilities = r"(?:bg|text|border|outline|ring|ring-offset|divide|decoration|accent|caret|fill|stroke|shadow|from|via|to|placeholder)"
palette = r"(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)"
named_colours = {
    "aliceblue", "antiquewhite", "aqua", "aquamarine", "azure", "beige", "bisque", "black",
    "blanchedalmond", "blue", "blueviolet", "brown", "burlywood", "cadetblue", "chartreuse",
    "chocolate", "coral", "cornflowerblue", "cornsilk", "crimson", "cyan", "darkblue", "darkcyan",
    "darkgoldenrod", "darkgray", "darkgreen", "darkgrey", "darkkhaki", "darkmagenta",
    "darkolivegreen", "darkorange", "darkorchid", "darkred", "darksalmon", "darkseagreen",
    "darkslateblue", "darkslategray", "darkslategrey", "darkturquoise", "darkviolet", "deeppink",
    "deepskyblue", "dimgray", "dimgrey", "dodgerblue", "firebrick", "floralwhite", "forestgreen",
    "fuchsia", "gainsboro", "ghostwhite", "gold", "goldenrod", "gray", "green", "greenyellow",
    "grey", "honeydew", "hotpink", "indianred", "indigo", "ivory", "khaki", "lavender",
    "lavenderblush", "lawngreen", "lemonchiffon", "lightblue", "lightcoral", "lightcyan",
    "lightgoldenrodyellow", "lightgray", "lightgreen", "lightgrey", "lightpink", "lightsalmon",
    "lightseagreen", "lightskyblue", "lightslategray", "lightslategrey", "lightsteelblue",
    "lightyellow", "lime", "limegreen", "linen", "magenta", "maroon", "mediumaquamarine",
    "mediumblue", "mediumorchid", "mediumpurple", "mediumseagreen", "mediumslateblue",
    "mediumspringgreen", "mediumturquoise", "mediumvioletred", "midnightblue", "mintcream",
    "mistyrose", "moccasin", "navajowhite", "navy", "oldlace", "olive", "olivedrab", "orange",
    "orangered", "orchid", "palegoldenrod", "palegreen", "paleturquoise", "palevioletred",
    "papayawhip", "peachpuff", "peru", "pink", "plum", "powderblue", "purple", "rebeccapurple",
    "red", "rosybrown", "royalblue", "saddlebrown", "salmon", "sandybrown", "seagreen",
    "seashell", "sienna", "silver", "skyblue", "slateblue", "slategray", "slategrey", "snow",
    "springgreen", "steelblue", "tan", "teal", "thistle", "tomato", "turquoise", "violet",
    "wheat", "white", "whitesmoke", "yellow", "yellowgreen",
}
# Any Tailwind utility (including variants and negative/important forms) whose
# arbitrary value is itself a hex color. Keeping the utility name structural
# covers the full color-capable vocabulary without treating issue references
# or HTML numeric entities as classes.
tailwind = re.compile(rf"(?<![\w-])(?:[a-z][\w-]*:)*!?-?[a-z][\w-]*-\[({hex_value})\](?![0-9a-fA-F])", re.IGNORECASE)
css_or_object = re.compile(rf"(?:^|[;{{,])\s*(?:--[\w-]+|[\w-]*(?:color|background|border|fill|stroke|shadow|outline|decoration|accent|caret)[\w-]*)\s*:\s*['\"]?({hex_value})(?![0-9a-fA-F])", re.IGNORECASE)
jsx_attribute = re.compile(rf"\b(?:color|fill|stroke)\s*=\s*(?:['\"]|{{\s*['\"])({hex_value})(?![0-9a-fA-F])", re.IGNORECASE)
jsx_colour_attribute = re.compile(r"\b(?:color|fill|stroke)\s*=\s*(?:['\"]|\{\s*['\"])([^'\"}\n]+)", re.IGNORECASE)
palette_scale = r"(?:50|[1-9]00|950)"
special_colour = r"(?:black|white|transparent|current|inherit)"
default_tailwind = re.compile(rf"(?<![\w-])(?:[a-z][\w-]*:)*!?-?({colour_utilities}-(?:(?:{palette})-{palette_scale}|{special_colour})(?:/\d{{1,3}})?)(?![\w-])", re.IGNORECASE)
arbitrary_tailwind = re.compile(rf"(?<![\w-])(?:[a-z][\w-]*:)*!?-?{colour_utilities}-\[([^\]\n]+)\]", re.IGNORECASE)
declaration = re.compile(rf"(?:^|[;{{,])\s*(?:--[\w-]+|[\w-]*{colour_properties}[\w-]*)\s*:\s*([^;}}\n]+)", re.IGNORECASE)
colour_function_start = re.compile(r"(?<![\w-])(?:rgb|rgba|hsl|hsla|oklch|oklab|color)\(", re.IGNORECASE)

def colour_functions(value: str) -> list[tuple[int, str]]:
    """Return balanced supported colour functions, including nested var()."""
    found: list[tuple[int, str]] = []
    cursor = 0
    while match := colour_function_start.search(value, cursor):
        depth = 1
        end = match.end()
        while end < len(value) and depth:
            if value[end] == "(":
                depth += 1
            elif value[end] == ")":
                depth -= 1
            end += 1
        if depth:
            cursor = match.end()
            continue
        found.append((match.start(), value[match.start():end].lower()))
        cursor = end
    return found

def complete_named_colour(value: str) -> tuple[int, str] | None:
    leading = len(value) - len(value.lstrip())
    candidate = value.strip()
    if (len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "'\""):
        candidate = candidate[1:-1].strip()
        leading += 1
    important = re.fullmatch(r"([a-z]+)(?:\s*!important)?", candidate, re.IGNORECASE)
    if important and important.group(1).lower() in named_colours:
        return leading, important.group(1).lower()
    return None

def non_hex_value_matches(contents: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for match in default_tailwind.finditer(contents):
        found.append((match.start(1), match.group(1).lower()))
    for match in arbitrary_tailwind.finditer(contents):
        value = match.group(1)
        lowered = value.lower()
        normalized = lowered.replace("_", " ")
        functions = colour_functions(normalized)
        if functions:
            for start, function in functions:
                found.append((match.start(1) + start, function.replace(" ", "_")))
        elif lowered in named_colours:
            found.append((match.start(1), lowered))
    for match in declaration.finditer(contents):
        value = match.group(1)
        base = match.start(1)
        for start, function in colour_functions(value):
            found.append((base + start, function))
        if named := complete_named_colour(value):
            start, candidate = named
            found.append((base + start, candidate))
    for match in jsx_colour_attribute.finditer(contents):
        value = match.group(1)
        base = match.start(1)
        for start, function in colour_functions(value):
            found.append((base + start, function))
        if named := complete_named_colour(value):
            start, candidate = named
            found.append((base + start, candidate))
    return found

def is_production_file(path: Path) -> bool:
    relative = path.relative_to(source_root)
    if path.suffix not in extensions:
        return False
    if any(part in excluded_directories for part in relative.parts[:-1]):
        return False
    if relative.as_posix() == "design-system/tokens/tokens.css":
        return False
    if ".test." in path.name or ".stories." in path.name:
        return False
    return True

files = sorted(path for path in source_root.rglob("*") if path.is_file() and is_production_file(path))
hits: list[tuple[str, int, int, str]] = []
for path in files:
    relative = path.relative_to(source_root).as_posix()
    contents = path.read_text(encoding="utf-8")
    matches: list[tuple[int, str]] = []
    for pattern in (tailwind, css_or_object, jsx_attribute):
        matches.extend((match.start(1), match.group(1).lower()) for match in pattern.finditer(contents))
    matches.extend(non_hex_value_matches(contents))
    for offset, value in sorted(set(matches)):
        line = contents.count("\n", 0, offset) + 1
        previous_newline = contents.rfind("\n", 0, offset)
        column = offset - previous_newline
        hits.append((relative, line, column, value))
hits.sort()

allowed: list[tuple[str, int, int, str, str]] = []
errors: list[str] = []
for number, raw in enumerate(allowlist_path.read_text(encoding="utf-8").splitlines(), 1):
    if not raw or raw.startswith("# path"):
        continue
    fields = raw.split("\t")
    if len(fields) != 5 or not all(fields):
        errors.append(f"invalid allowlist row {number}: expected path, line, column, value, and specific reason")
        continue
    path, raw_line, raw_column, value, reason = fields
    try:
        line, column = int(raw_line), int(raw_column)
    except ValueError:
        errors.append(f"invalid allowlist row {number}: line and column must be positive integers")
        continue
    if line < 1 or column < 1:
        errors.append(f"invalid allowlist row {number}: line and column must be positive integers")
        continue
    if value != value.lower() or any(character in value for character in "\t\r\n"):
        errors.append(f"invalid allowlist row {number}: value must be normalized lowercase scanner output")
        continue
    allowed.append((path, line, column, value, reason))

allowed_locations = [(path, line, column, value) for path, line, column, value, _reason in allowed]
unexpected = Counter(hits) - Counter(allowed_locations)
stale = Counter(allowed_locations) - Counter(hits)
reasons = {(path, line, column, value): reason for path, line, column, value, reason in allowed}
if len(reasons) != len(allowed):
    errors.append("invalid allowlist: duplicate source identity; each occurrence requires exactly one reason")
for (path, line, column, value), count in sorted(unexpected.items()):
    errors.extend([f"unlisted hit: {path}:{line}:{column}\t{value}"] * count)
for (path, line, column, value), count in sorted(stale.items()):
    reason = reasons[(path, line, column, value)]
    errors.extend([f"stale allowlist entry: {path}:{line}:{column}\t{value}\t{reason}"] * count)

hit_files = sorted({path for path, _line, _column, _value in hits})
print(f"Colour scan receipt: denominator={len(files)} production files; hits={len(hits)}; files={len(hit_files)}")
print("Colour scan files: " + (", ".join(hit_files) if hit_files else "(none)"))
if errors:
    print("FAIL: production colour baseline mismatch:", file=sys.stderr)
    for error in errors:
        print(f"  {error}", file=sys.stderr)
    sys.exit(3)
PY
}

# Unknown-token utility scan (THR-376). Tailwind v4 silently emits NO CSS for a
# colour/spacing/radius/shadow utility whose name is not a theme token (e.g.
# `bg-surface-page` when only `--color-surface-canvas` exists), so the class
# "works" in review and renders nothing. A name is defined when tokens.css
# declares it in an @theme block, or when the Tailwind default theme that
# tokens.css pulls in through `@import "tailwindcss"` declares it
# (node_modules/tailwindcss/theme.css). Raw/palette colours stay owned by
# scan_colours above. Only string literals (class lists) and CSS @apply lists
# are read; comments, JSX prose and the i18n copy catalogs are not class lists.
scan_tokens() {
  local source_root=${DESIGN_SYSTEM_SOURCE_ROOT:-src}
  local tokens=${DESIGN_SYSTEM_TOKENS:-$source_root/design-system/tokens/tokens.css}
  local default_theme=${DESIGN_SYSTEM_TAILWIND_THEME:-node_modules/tailwindcss/theme.css}

  python3 - "$source_root" "$tokens" "$default_theme" <<'PY'
from __future__ import annotations

from pathlib import Path
import os
import re
import sys

source_root, tokens_path, default_theme_path = (Path(argument) for argument in sys.argv[1:4])
for required in (tokens_path, default_theme_path):
    if not required.is_file():
        print(f"FAIL: unknown-token scan input missing: {required} (run npm ci for the Tailwind default theme)", file=sys.stderr)
        sys.exit(1)


def theme_names(text: str) -> set[str]:
    """Custom-property names (without the leading --) declared in every @theme block."""
    names: set[str] = set()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for match in re.finditer(r"@theme\b[^{;]*\{", text):
        depth, index = 0, match.end() - 1
        while index < len(text):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        names.update(re.findall(r"(?m)^\s*--([a-z0-9-]+)\s*:", text[match.end():index]))
    return names


def theme_resets(text: str) -> set[str]:
    """Namespaces that an @theme block clears with `--<namespace>-*: initial`."""
    resets: set[str] = set()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for match in re.finditer(r"@theme\b[^{;]*\{", text):
        end = text.find("}", match.end())
        resets.update(re.findall(r"(?m)^\s*--([a-z0-9-]+)-\*\s*:\s*initial\s*;", text[match.end():end if end >= 0 else len(text)]))
    return resets


# A namespace cleared in tokens.css (`--shadow-*: initial`, `--radius-*: initial`,
# THR-376 PR5) drops every Tailwind default-theme name in it: `shadow-sm` or
# `rounded-xl` then emit no CSS, so only the tokens.css re-declarations count.
reset_namespaces = theme_resets(tokens_path.read_text(encoding="utf-8"))
default_names = {
    name for name in theme_names(default_theme_path.read_text(encoding="utf-8"))
    if not any(name == namespace or name.startswith(namespace + "-") for namespace in reset_namespaces)
}
defined = theme_names(tokens_path.read_text(encoding="utf-8")) | default_names

# Spacing is Tailwind's linear scale: step N is N x 0.25rem (THR-376 PR4). A
# numeric --spacing-N declared in tokens.css with any other value silently
# re-maps every p-N/gap-N/h-N/... (the removed 5=24px..8=64px t-shirt remap).
linear_spacing_hits = []
tokens_text = re.sub(r"/\*.*?\*/", "", tokens_path.read_text(encoding="utf-8"), flags=re.S)
for match in re.finditer(r"(?m)^\s*--spacing-([0-9]+(?:\.[0-9]+)?)\s*:\s*([^;]*?)\s*;", tokens_text):
    step, value = match.group(1), match.group(2)
    expected = float(step) * 0.25
    value_match = re.fullmatch(r"([0-9]*\.?[0-9]+)(rem)?", value)
    ok = value_match is not None and (value_match.group(2) == "rem" or float(value_match.group(1)) == 0) and float(value_match.group(1)) == expected
    if not ok:
        linear_spacing_hits.append(f"--spacing-{step}: {value} (linear scale is {expected:g}rem)")
if linear_spacing_hits:
    print("FAIL: tokens.css re-maps Tailwind's linear numeric spacing scale (THR-376 PR4):", file=sys.stderr)
    for hit in linear_spacing_hits:
        print(f"  non-linear spacing token: {hit}", file=sys.stderr)
    sys.exit(5)

# One colour vocabulary (THR-376 PR10). Only the canonical semantic colour names
# may hold a raw colour value in tokens.css. Every other --color-* declaration
# (v3, tier-*, status-*, feedback-*, agent-*, id-*, shadcn and duplicate
# spellings), in any block and either theme, must be an alias of a canonical
# token: `var(--color-<semantic>)`, or for an alpha tint
# `color-mix(in oklab, var(--color-<semantic>) N%, transparent)`. An alias of an
# alias, or a literal, fails, so values cannot drift apart again.
semantic_colours = set((os.environ.get("DESIGN_SYSTEM_SEMANTIC_COLOURS") or
    "surface-canvas surface-sunken surface-raised surface-overlay surface-scrim surface-hover surface-subtle "
    "text-primary text-secondary text-muted text-inverse text-disabled "
    "border-default border-strong border-subtle "
    "accent-default accent-hover accent-soft accent-ring accent-text brand-foreground "
    "attention attention-soft attention-text info info-soft positive danger danger-soft").split())
semantic_pattern = "|".join(re.escape(name) for name in sorted(semantic_colours, key=len, reverse=True))
alias_value = re.compile(
    rf"var\(--color-(?:{semantic_pattern})\)"
    rf"|color-mix\(in oklab, var\(--color-(?:{semantic_pattern})\) [0-9]{{1,3}}%, transparent\)")
alias_hits = []
# Comments are blanked (newlines kept) so reported line numbers match the file.
tokens_lines = re.sub(r"/\*.*?\*/", lambda comment: re.sub(r"[^\n]", " ", comment.group()),
                      tokens_path.read_text(encoding="utf-8"), flags=re.S)
for match in re.finditer(r"(?m)^[ \t]*--color-([a-z0-9-]+)\s*:\s*([^;]*?)\s*;", tokens_lines):
    name, value = match.group(1), match.group(2)
    if name in semantic_colours:
        continue
    if not alias_value.fullmatch(value):
        line = tokens_lines.count("\n", 0, match.start(1)) + 1
        alias_hits.append(f"{tokens_path}:{line}\t--color-{name}: {value}")
if alias_hits:
    print("FAIL: tokens.css alias colours must be var() aliases of a semantic token, not literals (THR-376 PR10):", file=sys.stderr)
    for hit in alias_hits:
        print(f"  literal alias colour: {hit}", file=sys.stderr)
    sys.exit(6)

colour_roots = [
    "inset-shadow", "inset-ring", "ring-offset", "decoration", "outline", "divide", "accent", "caret",
    "stroke", "shadow", "border", "ring", "fill", "from", "via", "text", "bg", "to",
]
border_sides = ["border-x", "border-y", "border-t", "border-r", "border-b", "border-l", "border-s", "border-e"]
spacing_roots = [
    "scroll-mx", "scroll-my", "scroll-mt", "scroll-mr", "scroll-mb", "scroll-ml", "scroll-ms", "scroll-me", "scroll-m",
    "scroll-px", "scroll-py", "scroll-pt", "scroll-pr", "scroll-pb", "scroll-pl", "scroll-ps", "scroll-pe", "scroll-p",
    "translate-x", "translate-y", "space-x", "space-y", "inset-x", "inset-y", "min-w", "max-w", "min-h", "max-h",
    "gap-x", "gap-y", "indent", "basis", "inset", "bottom", "right", "start", "size", "left", "top", "end", "gap",
    "px", "py", "pt", "pr", "pb", "pl", "ps", "pe", "mx", "my", "mt", "mr", "mb", "ml", "ms", "me", "p", "m", "w", "h",
]
radius_roots = [
    "rounded-tl", "rounded-tr", "rounded-br", "rounded-bl", "rounded-ss", "rounded-se", "rounded-es", "rounded-ee",
    "rounded-t", "rounded-r", "rounded-b", "rounded-l", "rounded-s", "rounded-e", "rounded",
]
# Longest root first so `border-t-accent-default` splits as border-t + accent-default, and
# `outline-offset-2` is claimed (and skipped, numeric) before `outline`.
roots = sorted(set(colour_roots + border_sides + spacing_roots + radius_roots + ["outline-offset", "ring-inset"]), key=len, reverse=True)
css_keywords = {"transparent", "current", "inherit"}
# Bare `shadow` / `rounded` (and bare side/corner radius utilities) read the
# default theme too, so they also render nothing once their namespace is reset.
bare_reset_utilities = (({"shadow"} if "shadow" in reset_namespaces else set())
                        | (set(radius_roots) if "radius" in reset_namespaces else set()))
side_keywords = {"x", "y", "t", "r", "b", "l", "s", "e"}
corner_keywords = side_keywords | {"tl", "tr", "br", "bl", "ss", "se", "es", "ee"}
# Non-token keyword values of each utility family (Tailwind v4 static utilities).
static_values = {
    "bg": {"none", "fixed", "local", "scroll", "auto", "cover", "contain", "center", "top", "bottom", "left", "right",
           "left-top", "left-bottom", "right-top", "right-bottom", "top-left", "top-right", "bottom-left", "bottom-right",
           "repeat", "no-repeat", "repeat-x", "repeat-y", "repeat-round", "repeat-space", "clip-border", "clip-padding",
           "clip-content", "clip-text", "origin-border", "origin-padding", "origin-content"},
    "text": {"left", "center", "right", "justify", "start", "end", "ellipsis", "clip", "wrap", "nowrap", "balance", "pretty"},
    "border": {"solid", "dashed", "dotted", "double", "hidden", "none", "collapse", "separate"},
    "outline": {"none", "hidden", "solid", "dashed", "dotted", "double"},
    "ring": {"inset"},
    "divide": {"x", "y", "x-reverse", "y-reverse", "solid", "dashed", "dotted", "double", "none"},
    "decoration": {"solid", "double", "dotted", "dashed", "wavy", "auto", "from-font", "clone", "slice"},
    "accent": {"auto"}, "caret": {"auto"}, "fill": {"none"}, "stroke": {"none"},
    "shadow": {"none"}, "inset-shadow": {"none"},
    "spacing": {"auto", "full", "screen", "svw", "lvw", "dvw", "svh", "lvh", "dvh", "min", "max", "fit", "px", "none",
                "prose", "lh", "reverse", "x-reverse", "y-reverse"},
    "radius": {"none", "full"},
}


def namespace_of(root: str) -> str:
    """The @theme namespace a utility root reads its named values from."""
    if root in radius_roots:
        return "radius"
    if root in spacing_roots:
        return "spacing"
    return root


def namespace_defines(namespace: str, value: str) -> bool:
    return f"{namespace}-{value}" in defined


def resolves(root: str, value: str) -> bool:
    if root in ("outline-offset", "ring-inset"):
        return True
    if root in radius_roots:
        return value in static_values["radius"] or namespace_defines("radius", value)
    if root in spacing_roots:
        return (value in static_values["spacing"] or namespace_defines("spacing", value)
                or namespace_defines("container", value) or namespace_defines("breakpoint", value))
    family = "border" if root in border_sides else root
    if value in css_keywords or value in static_values.get(family, set()) or namespace_defines("color", value):
        return True
    if root == "text" and (namespace_defines("text", value) or value.startswith("shadow")):
        return True
    if root in ("shadow", "inset-shadow") and namespace_defines(root, value):
        return True
    if root == "bg" and re.match(r"(?:linear|radial|conic|gradient-to|blend|size|position)\b", value):
        return True
    return False


def class_lists(text: str, suffix: str):
    """(offset, body) for each JS/TS string literal, or each CSS @apply list; comments are skipped."""
    if suffix == ".css":
        clean = re.sub(r"/\*.*?\*/", lambda match: re.sub(r"[^\n]", " ", match.group()), text, flags=re.S)
        for match in re.finditer(r"@apply\s+([^;]+);", clean):
            yield match.start(1), match.group(1)
        return
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = size if end < 0 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = size if end < 0 else end + 2
        elif char in "\"'`":
            end = index + 1
            while end < size and text[end] != char:
                if text[end] == "\\":
                    end += 1
                elif char != "`" and text[end] == "\n":
                    break
                end += 1
            yield index + 1, text[index + 1:end]
            index = end + 1
        else:
            index += 1


word = re.compile(r"[^\s'\"`{}()$,?]+")
utility = re.compile(r"[a-z][a-z0-9-]*")


def unknown_utilities(text: str, suffix: str):
    for offset, body in class_lists(text, suffix):
        for match in word.finditer(body):
            candidate = re.sub(r"/\d+$", "", match.group().split(":")[-1].strip("!").lstrip("-"))
            if not utility.fullmatch(candidate):
                continue
            if candidate in bare_reset_utilities:
                yield offset + match.start(), candidate
                continue
            root = next((name for name in roots if candidate.startswith(name + "-")), None)
            if root is None:
                continue
            value = candidate[len(root) + 1:]
            # Numeric values (`p-4`, `ring-offset-2`) carry no token name. A digit
            # followed by letters (`2xl`, `2xs`) is a named scale key, though: in a
            # namespace cleared by tokens.css it must resolve like any other name.
            if not value[0].isalpha() and not (
                    namespace_of(root) in reset_namespaces and re.fullmatch(r"[0-9]+[a-z][a-z0-9-]*", value)):
                continue
            # Bare side utilities (`border-b`, `rounded-tl`) carry no token name.
            if (root == "border" and value in side_keywords) or (root == "rounded" and value in corner_keywords):
                continue
            if not resolves(root, value):
                yield offset + match.start(), candidate


extensions = {".css", ".js", ".jsx", ".ts", ".tsx"}
excluded_directories = {"__snapshots__", "__tests__", "reports", "screenshots", "test"}
files: list[str] = []
hits: list[tuple[str, int, int, str]] = []
for path in sorted(source_root.rglob("*")):
    if not path.is_file() or path.suffix not in extensions:
        continue
    relative = path.relative_to(source_root).as_posix()
    if any(part in excluded_directories for part in Path(relative).parts[:-1]):
        continue
    if relative == "design-system/tokens/tokens.css" or relative.startswith("lib/i18n/locales/"):
        continue
    if ".test." in path.name or ".stories." in path.name:
        continue
    files.append(relative)
    contents = path.read_text(encoding="utf-8")
    for offset, candidate in unknown_utilities(contents, path.suffix):
        line = contents.count("\n", 0, offset) + 1
        column = offset - contents.rfind("\n", 0, offset)
        hits.append((relative, line, column, candidate))
hits.sort()
print(f"Unknown-token scan receipt: denominator={len(files)} production files; theme tokens={len(defined)}; hits={len(hits)}")
if hits:
    print("FAIL: utility classes name theme tokens that are not defined (they render no CSS):", file=sys.stderr)
    for relative, line, column, candidate in hits:
        print(f"  unknown token utility: {relative}:{line}:{column}\t{candidate}", file=sys.stderr)
    sys.exit(4)
PY
}

if [ "${1:-}" = "--scan-hex" ]; then
  scan_colours
  exit $?
fi

if [ "${1:-}" = "--scan-tokens" ]; then
  scan_tokens
  exit $?
fi

echo "==> Typecheck"
npm run typecheck

echo "==> ESLint"
npm run lint

echo "==> Build Storybook"
npm run build-storybook

echo "==> Production colour baseline"
scan_colours

echo "==> Unknown-token utilities"
scan_tokens

echo "All design-system checks passed."
