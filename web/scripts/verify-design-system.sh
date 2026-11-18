#!/usr/bin/env bash
#
# CI gate for the web design system. Runs typecheck + lint + tests +
# deterministic Storybook static build + complete production-source colour scan
# + unknown-token utility scan + Pasture parity check.
#
# Exit codes:
#   0 — clean
#   1 — typecheck, lint, test, or build failure (or missing scan input)
#   3 — production colour baseline mismatch
#   4 — a colour/spacing/radius/shadow utility names an undefined theme token
#   5 — tokens.css re-maps a numeric --spacing-N off Tailwind's linear scale
#   6 — a non-semantic (alias) --color-* in tokens.css is not a var() alias of
#       a semantic token
#   7 — tokens.css or an owning component drifts from web/design/pasture-ds.css
#       Direction A on a pinned Pasture surface (topbar, page title, card,
#       nav row/active, ID)
#
# Modes: `--scan-hex` (colour baseline only), `--scan-tokens` (tokens.css
# spacing/alias checks, then unknown-token utilities), `--scan-pasture`
# (Pasture parity only).
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

# Pasture parity check (THR-376 PR12). web/design/pasture-ds.css Direction A is
# the visual authority. For a fixed set of Pasture surfaces this mode resolves
# what tokens.css (light @theme values, dark `:root[data-theme="dark"]`
# overrides, var() aliases followed) and the owning component's class list
# produce, and fails (exit 7) when it differs from the ds.css Direction A value
# in either theme. There is no allowlist or exemption: when ds.css changes,
# update tokens.css / the component so they agree again. Only unconditional
# rules are read: `@media` blocks (the <860px mobile topbar) and Direction B are
# ignored. Component classes are read at the desktop rail: an `md:` utility
# overrides the unprefixed one for the same property.
scan_pasture() {
  local source_root=${DESIGN_SYSTEM_SOURCE_ROOT:-src}
  local tokens=${DESIGN_SYSTEM_TOKENS:-$source_root/design-system/tokens/tokens.css}
  local reference=${DESIGN_SYSTEM_PASTURE_REFERENCE:-design/pasture-ds.css}
  local default_theme=${DESIGN_SYSTEM_TAILWIND_THEME:-node_modules/tailwindcss/theme.css}

  python3 - "$source_root" "$tokens" "$reference" "$default_theme" <<'PY'
from __future__ import annotations

from pathlib import Path
import re
import sys

source_root, tokens_path, reference_path, default_theme_path = (Path(argument) for argument in sys.argv[1:5])
components = {
    "AppBar": source_root / "design-system/layouts/AppShell/AppBar.tsx",
    "PageTitle": source_root / "design-system/patterns/PageTitle.tsx",
    "Card": source_root / "design-system/primitives/Card.tsx",
    "Sidebar": source_root / "design-system/layouts/AppShell/Sidebar.tsx",
    "IdBadge": source_root / "design-system/patterns/IdBadge.tsx",
}
for required in (tokens_path, reference_path, default_theme_path, *components.values()):
    if not required.is_file():
        print(f"FAIL: Pasture parity input missing: {required} (run npm ci for the Tailwind default theme)", file=sys.stderr)
        sys.exit(1)


class Unresolved(Exception):
    pass


def without_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def without_at_blocks(text: str) -> str:
    """Drop conditional (@media/@supports/@container) blocks with their bodies."""
    out, index = [], 0
    for match in re.finditer(r"@(?:media|supports|container)\b[^{;]*\{", text):
        if match.start() < index:
            continue
        depth, end = 0, match.end() - 1
        while end < len(text):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        out.append(text[index:match.start()])
        index = end + 1
    out.append(text[index:])
    return "".join(out)


def declarations(body: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for part in body.split(";"):
        if ":" in part:
            name, value = part.split(":", 1)
            if name.strip():
                found[name.strip()] = " ".join(value.split())
    return found


def css_rules(text: str) -> list[tuple[list[str], dict[str, str]]]:
    text = without_at_blocks(without_comments(text))
    rules = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        selectors = [" ".join(selector.split()) for selector in match.group(1).split(",")]
        rules.append((selectors, declarations(match.group(2))))
    return rules


reference_rules = css_rules(reference_path.read_text(encoding="utf-8"))


def reference_rule(selector: str) -> dict[str, str]:
    merged: dict[str, str] = {}
    for selectors, decls in reference_rules:
        if selector in selectors:
            merged.update(decls)
    return merged


def reference_has(selector: str) -> bool:
    return any(selector in selectors for selectors, _decls in reference_rules)


themes = ("light", "dark")
reference_vars = {
    theme: {name: value for name, value in {**reference_rule('[data-dir="a"]'),
                                             **reference_rule(f'[data-dir="a"][data-theme="{theme}"]')}.items()
            if name.startswith("--")}
    for theme in themes
}


def resolve(value: str, variables: dict[str, str], depth: int = 0) -> str:
    if depth > 12:
        raise Unresolved(f"var() cycle in {value}")
    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise Unresolved(f"undefined {name}")
        return resolve(variables[name], variables, depth + 1)
    return re.sub(r"var\((--[a-z0-9-]+)\)", substitute, value)


def reference_value(selector: str, prop: str, theme: str) -> str:
    """Direction-A cascade: `[data-dir="a"] <selector>` overrides `<selector>`."""
    value = {**reference_rule(selector), **reference_rule(f'[data-dir="a"] {selector}')}.get(prop)
    if value is None:
        raise Unresolved(f"ds.css sets no {prop} on {selector}")
    return resolve(value, reference_vars[theme])


def theme_blocks(text: str, opener: str) -> dict[str, str]:
    text = without_comments(text)
    found: dict[str, str] = {}
    for match in re.finditer(opener, text):
        depth, end = 0, match.end() - 1
        while end < len(text):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        found.update({name: value for name, value in declarations(text[match.end():end]).items() if name.startswith("--")})
    return found


tokens_text = tokens_path.read_text(encoding="utf-8")
light_tokens = {**theme_blocks(tokens_text, r"@theme\b[^{;]*\{"), **theme_blocks(tokens_text, r"(?m)^:root\s*\{")}
reset_namespaces = {name[2:-2] for name, value in light_tokens.items() if name.endswith("-*") and value == "initial"}
default_tokens = {
    name: value for name, value in theme_blocks(default_theme_path.read_text(encoding="utf-8"), r"@theme\b[^{;]*\{").items()
    if not any(name == f"--{namespace}" or name.startswith(f"--{namespace}-") for namespace in reset_namespaces)
}
token_vars = {
    "light": {**default_tokens, **light_tokens},
    "dark": {**default_tokens, **light_tokens, **theme_blocks(tokens_text, r':root\[data-theme="dark"\]\s*\{')},
}


def token(name: str, theme: str) -> str:
    if name not in token_vars[theme]:
        raise Unresolved(f"tokens.css defines no {name}")
    return resolve(token_vars[theme][name], token_vars[theme])


number = re.compile(r"-?(?:\d+\.?\d*|\.\d+)")


def normal(value: str) -> str:
    value = " ".join(value.lower().replace(", ", ",").replace("( ", "(").replace(" )", ")").split())
    value = re.sub(r"\s*,\s*", ",", value)
    value = re.sub(r"([\d.]+)rem\b", lambda match: f"{float(match.group(1)) * 16:g}px", value)
    value = number.sub(lambda match: f"{float(match.group()):g}", value)
    return value


def px(value: str) -> str:
    return normal(value if value != "0" else "0px")


# ---- component class lists -------------------------------------------------
def source(name: str) -> str:
    text = components[name].read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))


def extract(name: str, pattern: str, what: str, group: int = 1) -> list[str]:
    match = re.search(pattern, source(name), flags=re.S)
    if not match:
        raise Unresolved(f"{components[name]}: cannot locate {what}")
    return match.group(group).split()


def layers(classes: list[str]) -> list[list[str]]:
    """Unprefixed utilities, then `md:` utilities (desktop rail); other variants are state-only."""
    return [[c for c in classes if ":" not in c], [c[3:] for c in classes if c.startswith("md:") and c.count(":") == 1]]


def spacing(step: str, theme: str = "light") -> str:
    if f"--spacing-{step}" in token_vars[theme]:
        return px(token(f"--spacing-{step}", theme))
    if re.fullmatch(r"\d+(?:\.\d+)?", step):
        base = float(normal(token("--spacing", theme)).removesuffix("px"))
        return px(f"{float(step) * base:g}px")
    raise Unresolved(f"no spacing token {step}")


def box(classes: list[str], prefix: str) -> str:
    """Resolved `top right bottom left` padding: p-, then px/py-, then side utilities, per layer."""
    sides = {"top": "0px", "right": "0px", "bottom": "0px", "left": "0px"}
    targets = {"": ("top", "right", "bottom", "left"), "x": ("left", "right"), "y": ("top", "bottom"),
               "t": ("top",), "r": ("right",), "b": ("bottom",), "l": ("left",)}
    for layer in layers(classes):
        for axis in ("", "x", "y", "t", "r", "b", "l"):
            for utility in layer:
                match = re.fullmatch(rf"{prefix}{axis}-(.+)", utility)
                if match:
                    for side in targets[axis]:
                        sides[side] = spacing(match.group(1))
    return " ".join(sides[side] for side in ("top", "right", "bottom", "left"))


def last(classes: list[str], pattern: str) -> str | None:
    found = None
    for layer in layers(classes):
        for utility in layer:
            if re.fullmatch(pattern, utility):
                found = utility
    return found


def colour(classes: list[str], prefix: str, theme: str) -> str:
    for utility in reversed(layers(classes)[0]):
        match = re.fullmatch(rf"{prefix}-([a-z0-9-]+)(?:/(\d+))?", utility)
        if match and f"--color-{match.group(1)}" in token_vars[theme]:
            value = token(f"--color-{match.group(1)}", theme)
            if match.group(2):
                return normal(f"color-mix(in oklab, {value} {match.group(2)}%, transparent)")
            return normal(value)
    raise Unresolved(f"no {prefix}-<colour token> class")


def text_size(classes: list[str]) -> str:
    """The last `text-<name>` utility whose name is a --text-* size token (not a colour)."""
    found = None
    for layer in layers(classes):
        for utility in layer:
            if utility.startswith("text-") and f"--{utility}" in token_vars["light"]:
                found = utility[5:]
    if found is None:
        raise Unresolved("no text-<size token> class")
    return found


def font_size(classes: list[str]) -> str:
    return px(token(f"--text-{text_size(classes)}", "light"))


def font_weight(classes: list[str]) -> str:
    utility = last(classes, r"font-(?:thin|extralight|light|normal|medium|semibold|bold|extrabold|black)")
    if utility:
        return normal(token(f"--{utility.replace('font-', 'font-weight-', 1)}", "light"))
    name = f"--text-{text_size(classes)}--font-weight"
    return normal(token(name, "light")) if name in token_vars["light"] else "400"


def tracking(classes: list[str]) -> str:
    utility = last(classes, r"tracking-(?:\[[^\]]+\]|[a-z]+)")
    if utility:
        arbitrary = re.fullmatch(r"tracking-\[([^\]]+)\]", utility)
        return em_px(arbitrary.group(1) if arbitrary else token(f"--{utility}", "light"), classes)
    name = f"--text-{text_size(classes)}--letter-spacing"
    return em_px(token(name, "light"), classes) if name in token_vars["light"] else "0px"


def em_px(value: str, classes: list[str]) -> str:
    """Letter-spacing in px; an em value is scaled by the element's resolved font size."""
    em = re.fullmatch(r"\s*(-?[\d.]+)em\s*", value)
    if em:
        return px(f"{float(em.group(1)) * float(font_size(classes).removesuffix('px')):g}px")
    return px(value)


def family(classes: list[str]) -> str:
    utility = last(classes, r"font-(?:display|mono|sans|serif)")
    if not utility:
        raise Unresolved("no font-<family> class")
    return token(f"--{utility}", "light").split(",")[0].strip().strip("'\"").lower()


def reference_family(selector: str) -> str:
    return reference_value(selector, "font-family", "light").split(",")[0].strip().strip("'\"").lower()


def expand_box(value: str) -> str:
    parts = [px(part) for part in value.split()]
    top = parts[0]
    right = parts[1] if len(parts) > 1 else top
    bottom = parts[2] if len(parts) > 2 else top
    left = parts[3] if len(parts) > 3 else right
    return f"{top} {right} {bottom} {left}"


checked = 0
drift: list[str] = []


def check(surface: str, prop: str, expected, actual) -> None:
    """expected/actual are zero-argument callables so a failure is reported, never raised."""
    global checked
    checked += 1
    try:
        want = expected()
    except Unresolved as error:
        drift.append(f"{surface} {prop}: ds.css reference unresolved ({error})")
        return
    try:
        got = actual()
    except Unresolved as error:
        drift.append(f"{surface} {prop}: implementation unresolved ({error}); ds.css wants {want}")
        return
    if want != got:
        drift.append(f"{surface} {prop}: ds.css {want} != implementation {got}")


def safe(function):
    try:
        return function()
    except Unresolved as error:
        drift.append(str(error))
        return []


appbar_root = safe(lambda: extract("AppBar", r"export function AppBar\b.*?className=\"([^\"]+)\"", "the AppBar root className"))
appbar_title = safe(lambda: extract("AppBar", r"<span className=\"([^\"]+)\">\{title\}</span>", "the AppBar title <span>"))
page_title = safe(lambda: extract("PageTitle", r"<h1\s+className=\{`([^`$]+)", "the PageTitle <h1> className"))
card_base = safe(lambda: extract("Card", r"cva\(\s*'([^']+)'", "the Card cva base classes"))
card_container = safe(lambda: extract("Card", r"container:\s*'([^']+)'", "the Card container variant"))
card_padding = safe(lambda: extract("Card", r"padding:\s*\{\s*default:\s*'([^']+)'", "the Card default padding"))
card = card_base + card_container + card_padding
nav_pattern = r"export function SidebarNavItem\b.*?<NavLink\b.*?`([^`$]*)\$\{\s*isActive\s*\?\s*'([^']*)'"
nav_row = safe(lambda: extract("Sidebar", nav_pattern, "the SidebarNavItem NavLink class template"))
nav_active = nav_row + safe(lambda: extract("Sidebar", nav_pattern, "the SidebarNavItem active branch", 2))
nav_disabled = safe(lambda: extract("Sidebar", r"export function SidebarNavItem\b.*?<span\s+className=\"([^\"]*cursor-not-allowed[^\"]*)\"",
                                    "the disabled SidebarNavItem row"))
id_span = safe(lambda: extract("IdBadge", r"<span className=\{`([^`$]*)\$\{TEXT_COLOR", "the IdBadge <span> className"))
id_kinds = dict(re.findall(r"(\w+):\s*'([^']+)'", (re.search(r"const TEXT_COLOR\b[^{]*=\s*\{(.*?)\};", source("IdBadge"), re.S) or [None, ""])[1]))
if not id_kinds:
    drift.append(f"{components['IdBadge']}: cannot locate the TEXT_COLOR kind map")

for theme in themes:
    # Topbar (.topbar)
    check("topbar", f"background [{theme}]", lambda: normal(reference_value(".topbar", "background", theme)),
          lambda: colour(appbar_root, "bg", theme))
    check("topbar", f"border-bottom colour [{theme}]", lambda: normal(reference_value(".topbar", "border-bottom", theme).split(" ", 2)[2]),
          lambda: colour(appbar_root, "border", theme))
    # Card (.card)
    check("card", f"surface [{theme}]", lambda: normal(reference_value(".card", "background", theme)), lambda: colour(card, "bg", theme))
    check("card", f"border colour [{theme}]", lambda: normal(reference_value(".card", "border", theme).split(" ", 2)[2]),
          lambda: colour(card, "border", theme))
    # Nav active ([data-dir="a"] .nav-item.active)
    check("nav-active", f"background [{theme}]", lambda: normal(reference_value(".nav-item.active", "background", theme)),
          lambda: colour(nav_active, "bg", theme))
    check("nav-active", f"text colour [{theme}]", lambda: normal(reference_value(".nav-item.active", "color", theme)),
          lambda: colour(nav_active, "text", theme))
    # ID (.id): every IdBadge kind resolves to --info
    for kind, utility in sorted(id_kinds.items()):
        check("id", f"colour {kind} [{theme}]", lambda: normal(reference_value(".id", "color", theme)),
              lambda: colour(id_span + [utility], "text", theme))

check("topbar", "height", lambda: px(reference_value(".topbar", "height", "light")),
      lambda: spacing(re.fullmatch(r"h-(.+)", last(appbar_root, r"h-.+") or "h-?").group(1)))
check("topbar", "padding", lambda: expand_box(reference_value(".topbar", "padding", "light")), lambda: box(appbar_root, "p"))
check("topbar", "gap", lambda: px(reference_value(".topbar", "gap", "light")),
      lambda: spacing((last(appbar_root, r"gap-.+") or "gap-?")[4:]))
check("topbar", "border width", lambda: px(reference_value(".topbar", "border-bottom", "light").split()[0]),
      lambda: "1px" if last(appbar_root, r"border-b") else "0px")
check("page title", "font family", lambda: reference_family(".h1"), lambda: family(page_title))
check("page title", "font size", lambda: px(reference_value(".h1", "font-size", "light")), lambda: font_size(page_title))
check("page title", "font weight", lambda: normal(reference_value(".h1", "font-weight", "light")), lambda: font_weight(page_title))
check("page title", "letter-spacing", lambda: px(reference_value(".h1", "letter-spacing", "light")), lambda: tracking(page_title))
check("appbar title", "font family", lambda: reference_family(".page-title"), lambda: family(appbar_title))
check("appbar title", "font size", lambda: px(reference_value(".page-title", "font-size", "light")), lambda: font_size(appbar_title))
check("appbar title", "font weight", lambda: normal(reference_value(".page-title", "font-weight", "light")), lambda: font_weight(appbar_title))
check("appbar title", "letter-spacing", lambda: px(reference_value(".page-title", "letter-spacing", "light")), lambda: tracking(appbar_title))
check("card", "radius", lambda: px(reference_value(".card", "border-radius", "light")),
      lambda: px(token(f"--radius-{(last(card, r'rounded-[a-z0-9-]+') or 'rounded-?')[8:]}", "light")))
check("card", "padding", lambda: expand_box(reference_value(".card-pad", "padding", "light")), lambda: box(card, "p"))
check("card", "border width", lambda: px(reference_value(".card", "border", "light").split()[0]),
      lambda: "1px" if last(card, r"border") else "0px")
check("card", "shadow", lambda: normal(reference_value(".card", "box-shadow", "light")),
      lambda: normal(token(f"--shadow-{(last(card, r'shadow-[a-z0-9-]+') or 'shadow-?')[7:]}", "light")))
check("nav-active", "font weight", lambda: normal(reference_value(".nav-item.active", "font-weight", "light")),
      lambda: font_weight(nav_active))
check("nav-active", "box-shadow", lambda: normal(reference_value(".nav-item.active", "box-shadow", "light")),
      lambda: "none" if not any(re.fullmatch(r"(?:[a-z-]+:)*!?shadow-.+", c) and not c.startswith("focus") for c in nav_active) else "a shadow-* class")
check("nav-active", "left bar", lambda: "present" if reference_has('[data-dir="a"] .nav-item.active::before') or reference_has(".nav-item.active::before") else "none",
      lambda: "present" if any(c.split(":")[-1].startswith(("border-l", "border-s")) or c.startswith(("before:", "after:")) for c in nav_active) else "none")
for label, row in (("nav row", nav_row), ("nav row (disabled)", nav_disabled)):
    check(label, "padding", lambda: expand_box(reference_value(".nav-item", "padding", "light")), lambda: box(row, "p"))
    check(label, "gap", lambda: px(reference_value(".nav-item", "gap", "light")), lambda: spacing((last(row, r"gap-.+") or "gap-?")[4:]))
check("id", "font family", lambda: reference_family(".id"), lambda: family(id_span))
check("id", "font size", lambda: px(reference_value(".id", "font-size", "light")), lambda: font_size(id_span))
check("id", "font weight", lambda: normal(reference_value(".id", "font-weight", "light")), lambda: font_weight(id_span))

print(f"Pasture parity receipt: reference={reference_path}; tokens={tokens_path}; checks={checked}; drift={len(drift)}")
if drift:
    print("FAIL: tokens.css / owning components drift from web/design/pasture-ds.css Direction A (THR-376 PR12):", file=sys.stderr)
    for line in drift:
        print(f"  pasture drift: {line}", file=sys.stderr)
    sys.exit(7)
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

if [ "${1:-}" = "--scan-pasture" ]; then
  scan_pasture
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

echo "==> Pasture parity"
scan_pasture

echo "All design-system checks passed."
