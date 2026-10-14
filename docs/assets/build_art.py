"""Generate the README's animated SVGs (light + dark variants).

    python3 docs/assets/build_art.py

Writes hero-{light,dark}.svg and context-scaling-{light,dark}.svg next to this
file. Pure stdlib. Animations are CSS-only (no JS, no web fonts) so they play
inside GitHub's <img> sandbox, and respect prefers-reduced-motion by showing
the final frame.

The hero replays a real session: in a demo repo whose retry logic lives in
src/http/backoff.py (which never says "retry"), grep finds nothing and
`agent-nav find` returns that file. Chart numbers come from docs/benchmark.md.
"""
import pathlib
from html import escape

OUT = pathlib.Path(__file__).parent

MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"

# GitHub-like surfaces + the dataviz reference categorical slots 1 (blue) and 2 (orange)
THEMES = {
    "light": dict(
        page="#ffffff", card="#ffffff", border="#d0d7de", chrome="#f6f8fa",
        text="#1f2328", muted="#59636e", faint="#818b98", grid="#e6e9ec",
        prompt="#1a7f37", warn="#bc4c00", accent="#0969da",
        hit="#ddf4ff", hit_edge="#54aeff",
        s_nav="#2a78d6", s_index="#eb6834",
    ),
    "dark": dict(
        page="#0d1117", card="#0d1117", border="#3d444d", chrome="#151b23",
        text="#f0f6fc", muted="#9198a1", faint="#656c76", grid="#262c36",
        prompt="#3fb950", warn="#f0883e", accent="#4493f8",
        hit="#121d2f", hit_edge="#1f6feb",
        s_nav="#3987e5", s_index="#d95926",
    ),
}

CH = 9.0  # forced advance per monospace char (textLength), so typing reveals line up


# ---------------------------------------------------------------- hero

LOOP = 13.0  # seconds per hero loop


def pct(t):
    return f"{100 * t / LOOP:.2f}%"


def hero(c):
    W, H = 880, 330
    x0, y0, lh = 44, 92, 30  # text origin + line height inside the terminal
    fade_out = (LOOP - 1.2, LOOP - 0.6)

    lines = [
        # (start, kind, segments) ; segments = [(text, color-key)]
        (0.3, "show", [("# agent: \"where are retries configured?\"", "muted")]),
        (1.0, "type", [("$ ", "prompt"), ('grep -rn "retry" src/', "text")]),
        (2.6, "show", [("(no matches)", "warn")]),
        (3.1, "show", [("# the code says \"backoff\" and \"attempts\", never \"retry\"", "faint")]),
        (4.4, "type", [("$ ", "prompt"), ('agent-nav find "where are retries configured"', "text")]),
        (6.6, "hit", [("src/http/backoff.py", "accent"),
                      ("   retries failed HTTP calls with exponential backoff", "text")]),
    ]

    css, body, kf = [], [], []
    for i, (start, kind, segs) in enumerate(lines):
        y = y0 + i * lh + (8 if i >= 4 else 0) + (8 if i >= 5 else 0)
        text = "".join(s for s, _ in segs)
        n = len(text)
        cls = f"l{i}"
        # whole-line fade in at `start`, fade out with the loop
        kf.append(
            f"@keyframes {cls}{{0%,{pct(start)}{{opacity:0}}"
            f"{pct(start + 0.25)},{pct(fade_out[0])}{{opacity:1}}"
            f"{pct(fade_out[1])},100%{{opacity:0}}}}"
        )
        css.append(f".{cls}{{animation:{cls} {LOOP}s linear infinite}}")
        tspans = "".join(
            f'<tspan fill="{c[k]}">{escape(s)}</tspan>' for s, k in segs
        )
        g = []
        if kind == "hit":
            g.append(
                f'<rect x="{x0 - 12}" y="{y - 21}" width="{n * CH + 24}" height="31" rx="6" '
                f'fill="{c["hit"]}" stroke="{c["hit_edge"]}" stroke-width="1"/>'
            )
        g.append(
            f'<text x="{x0}" y="{y}" textLength="{n * CH:.0f}" lengthAdjust="spacing">{tspans}</text>'
        )
        if kind == "type":
            # a cover rect in the card colour slides right in character steps,
            # revealing everything after the "$ " prompt
            k = len(segs[0][0])
            m = n - k
            x = x0 + k * CH - 1
            dur = 0.045 * m
            ck = f"c{i}"
            kf.append(
                f"@keyframes {ck}{{0%,{pct(start)}{{transform:translateX(0)}}"
                f"{pct(start + dur)},100%{{transform:translateX({m * CH + 4:.0f}px)}}}}"
            )
            css.append(f".{ck}{{animation:{ck} {LOOP}s steps({m},end) infinite}}")
            g.append(
                f'<rect class="{ck}" x="{x:.0f}" y="{y - 20}" width="{m * CH + 8:.0f}" '
                f'height="28" fill="{c["card"]}" style="transform:translateX({m * CH + 4:.0f}px)"/>'
            )
        body.append(f'<g class="{cls}">{"".join(g)}</g>')

    # stat caption under the hit
    cap_t = 7.4
    kf.append(
        f"@keyframes cap{{0%,{pct(cap_t)}{{opacity:0;transform:translateY(6px)}}"
        f"{pct(cap_t + 0.4)},{pct(fade_out[0])}{{opacity:1;transform:translateY(0)}}"
        f"{pct(fade_out[1])},100%{{opacity:0}}}}"
    )
    css.append(f".cap{{animation:cap {LOOP}s ease-out infinite}}")
    cap_y = H - 34
    body.append(
        f'<g class="cap"><text x="{x0}" y="{cap_y}" style="font-family:{SANS};font-size:14px" '
        f'fill="{c["muted"]}">'
        f'<tspan fill="{c["prompt"]}" font-weight="600">✓</tspan>'
        f'<tspan dx="8">found by purpose, not by string</tspan>'
        f'<tspan dx="14" fill="{c["faint"]}">·</tspan>'
        f'<tspan dx="14">one tool call</tspan>'
        f'<tspan dx="14" fill="{c["faint"]}">·</tspan>'
        f'<tspan dx="14">no model call</tspan></text></g>'
    )

    # reduced motion: no animation, so every element keeps its final-frame style
    reduced = "@media (prefers-reduced-motion: reduce){*{animation:none!important}}"

    dots = "".join(
        f'<circle cx="{28 + k * 20}" cy="22" r="6" fill="{col}"/>'
        for k, col in enumerate(("#ff5f57", "#febc2e", "#28c840"))
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t d">
<title id="t">agent-nav finds a file by purpose where grep finds nothing</title>
<desc id="d">An agent asks where retries are configured. grep -rn "retry" src/ returns no matches because the code says backoff and attempts. agent-nav find "where are retries configured" returns src/http/backoff.py, retries failed HTTP calls with exponential backoff.</desc>
<style>
text{{font-family:{MONO};font-size:15px;white-space:pre}}
{"".join(kf)}
{"".join(css)}
{reduced}
</style>
<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12" fill="{c["card"]}" stroke="{c["border"]}"/>
<path d="M0.5 44V12.5a12 12 0 0 1 12-12h{W - 25}a12 12 0 0 1 12 12V44z" fill="{c["chrome"]}"/>
<line x1="0.5" y1="44" x2="{W - 0.5}" y2="44" stroke="{c["border"]}"/>
{dots}
<text x="{W / 2}" y="27" text-anchor="middle" fill="{c["muted"]}" style="font-size:13px">~/my-app</text>
<clipPath id="card"><rect x="1" y="45" width="{W - 2}" height="{H - 46}" rx="11"/></clipPath>
<g clip-path="url(#card)">{"".join(body)}</g>
</svg>
"""


# ---------------------------------------------------------------- chart

def chart(c):
    W, H = 880, 330
    left, right = 196, 96   # label gutter / value gutter
    plot_w = W - left - right
    vmax = 9442
    bar_h, gap = 22, 6      # bar thickness, surface gap between paired bars
    groups = [
        ("1× repo", "44 files", [("index", 990), ("nav", 17)]),
        ("10× repo", "368 files", [("index", 9442), ("nav", 17)]),
    ]
    color = {"index": c["s_index"], "nav": c["s_nav"]}

    css = [
        f"text{{font-family:{SANS}}}",
        ".b{transform-box:fill-box;transform-origin:0 50%;"
        "animation:grow 1.4s cubic-bezier(.2,.7,.2,1) both}",
        ".v{animation:fade .5s ease-out both}",
        "@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}",
        "@keyframes fade{from{opacity:0}to{opacity:1}}",
        "@media (prefers-reduced-motion: reduce){*{animation:none!important}}",
    ]

    out = []
    y = 122
    for gi, (name, sub, bars) in enumerate(groups):
        gy = y
        out.append(
            f'<text x="{left - 20}" y="{gy + bar_h - 2}" text-anchor="end" font-size="15" '
            f'font-weight="600" fill="{c["text"]}">{name}</text>'
            f'<text x="{left - 20}" y="{gy + bar_h + 18}" text-anchor="end" font-size="13" '
            f'fill="{c["muted"]}">{sub}</text>'
        )
        for bi, (key, v) in enumerate(bars):
            by = gy + bi * (bar_h + gap)
            w = max(plot_w * v / vmax, 3)  # 3px stub keeps tiny values visible
            r = min(4, w / 2)
            delay = 0.3 + gi * 0.9 + bi * 0.15
            dur = 1.4 + (1.2 if v == vmax else 0)
            # square at the baseline, 4px rounded data end
            d = (f"M{left} {by}h{w - r:.2f}a{r} {r} 0 0 1 {r} {r}v{bar_h - 2 * r}"
                 f"a{r} {r} 0 0 1 {-r} {r}h{-(w - r):.2f}z")
            out.append(
                f'<path class="b" d="{d}" fill="{color[key]}" '
                f'style="animation-delay:{delay:.2f}s;animation-duration:{dur:.1f}s"/>'
            )
            label = f"{v:,}"
            out.append(
                f'<text class="v" x="{left + w + 10:.1f}" y="{by + bar_h - 6}" font-size="14" '
                f'font-weight="600" fill="{c["text"]}" '
                f'style="animation-delay:{delay + dur - 0.3:.2f}s">{label}'
                f'<tspan font-weight="400" fill="{c["muted"]}"> tok</tspan></text>'
            )
        y += 2 * bar_h + gap + 52

    base_x = left
    axis = (f'<line x1="{base_x}" y1="108" x2="{base_x}" y2="{y - 40}" '
            f'stroke="{c["grid"]}" stroke-width="1"/>')

    legend = []
    lx = 32
    for key, txt in (("index", "loaded index.md"), ("nav", "agent-nav")):
        legend.append(
            f'<rect x="{lx}" y="72" width="12" height="12" rx="3" fill="{color[key]}"/>'
            f'<text x="{lx + 19}" y="83" font-size="13" fill="{c["muted"]}">{txt}</text>'
        )
        lx += 19 + len(txt) * 7.6 + 24

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t d">
<title id="t">Upfront context per task: loaded index.md vs agent-nav</title>
<desc id="d">Tokens loaded into context before the agent starts. On the 44-file benchmark repo: loaded index.md 990, agent-nav 17. On the 10x repo with 368 files: loaded index.md 9,442, agent-nav 17.</desc>
<style>{"".join(css)}</style>
<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12" fill="{c["card"]}" stroke="{c["border"]}"/>
<text x="32" y="40" font-size="17" font-weight="600" fill="{c["text"]}">Upfront context per task</text>
<text x="32" y="60" font-size="13" fill="{c["muted"]}">Tokens in the agent's context before it starts working, on the benchmark repo at 1× and 10× size</text>
{"".join(legend)}
{axis}
{"".join(out)}
<text x="32" y="{H - 22}" font-size="12" fill="{c["faint"]}">Retrieval quality was similar for both methods at both sizes. Method and caveats: docs/benchmark.md</text>
</svg>
"""


def main():
    for name, c in THEMES.items():
        (OUT / f"hero-{name}.svg").write_text(hero(c))
        (OUT / f"context-scaling-{name}.svg").write_text(chart(c))
    print("wrote", ", ".join(sorted(p.name for p in OUT.glob("*.svg"))))


if __name__ == "__main__":
    main()
