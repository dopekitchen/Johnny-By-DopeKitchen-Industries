"""Curated Ollama models, grouped by the hardware they need.

All of these can both SEE (game mode, "look at my screen") and use TOOLS (control the PC).
Any other Ollama tag can still be typed in the setup wizard.
"""

CATALOG = [
    # tag, display, download GB, VRAM GB it occupies on GPU, min RAM GB for CPU-only, notes
    {"tag": "qwen3.5:2b",  "name": "Qwen 3.5 2B",   "size": 1.9, "vram": 3,  "ram": 8,
     "notes": "Tiny and very fast. For older PCs and laptops."},
    {"tag": "qwen3.5:4b",  "name": "Qwen 3.5 4B",   "size": 3.4, "vram": 4.5, "ram": 12,
     "notes": "Recommended: fast, accurate, leaves your GPU for games."},
    {"tag": "gemma4:e4b",  "name": "Gemma 4 E4B",   "size": 9.6, "vram": 7,  "ram": 16,
     "notes": "Efficient edge model, natural conversation."},
    {"tag": "qwen3.5:9b",  "name": "Qwen 3.5 9B",   "size": 6.6, "vram": 8,  "ram": 16,
     "notes": "Smarter all-rounder for 10-12 GB GPUs."},
    {"tag": "gemma4:12b",  "name": "Gemma 4 12B",   "size": 7.7, "vram": 10, "ram": 24,
     "notes": "Strong reasoning; best with 12 GB+ VRAM."},
    {"tag": "gemma4:26b",  "name": "Gemma 4 26B",   "size": 18.0, "vram": 16, "ram": 28,
     "notes": "Top quality, but fills a 12 GB GPU - heavy while gaming."},
]

# Keep this much VRAM free for games when recommending a model.
GAMING_HEADROOM_GB = 6


def recommend(hw: dict) -> str:
    """Strongest model that still leaves room on the GPU for games (or fits RAM on CPU-only PCs)."""
    vram, ram = hw.get("best_vram_gb", 0), hw.get("ram_gb", 8)
    budget = max(vram * 0.6, vram - GAMING_HEADROOM_GB)   # GPU memory Johnny may use, the rest is for games
    best = CATALOG[0]["tag"]
    for m in CATALOG:
        if vram >= 4 and m["vram"] <= budget:
            best = m["tag"]
        elif vram < 4 and ram >= m["ram"] and m["size"] <= 4:
            best = m["tag"]
    if vram >= 6 and best not in ("qwen3.5:2b",):
        best = "qwen3.5:4b"   # tested best balance: all tool tasks pass with ~4 GB VRAM
    return best


def fit_label(m: dict, hw: dict) -> str:
    vram, ram = hw.get("best_vram_gb", 0), hw.get("ram_gb", 8)
    if vram >= m["vram"] + GAMING_HEADROOM_GB - 2:
        return "Great (room for games)"
    if vram >= m["vram"]:
        return "Fits GPU (tight in games)"
    if vram >= 8 and ram >= m["ram"]:
        return "GPU + RAM (heavy)"
    if ram >= m["ram"]:
        return "Runs on CPU (slower)"
    return "Too heavy for this PC"
