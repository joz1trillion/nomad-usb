"""Optional Nomad apps the user can choose to bundle on the USB.

The catalog below is vendored from Nomad's own service_seeder.js
(database/seeders/service_seeder.js in the upstream admin image).
Keep this in sync when Nomad bumps versions — verify against the
seeder by extracting the admin image and reading
  /app/database/seeders/service_seeder.js

When the user picks an app in the wizard, the corresponding image(s)
get added to the docker pull / docker save list. The compose file is
not modified — Nomad's Command Center handles starting these as
sibling containers via the host docker socket. Pre-bundling the
images means Nomad can install them offline without ever needing to
pull anything itself.

Each entry's `images` is a list because some apps have dependencies
(AI Assistant needs both Ollama and Qdrant). The first image is the
"primary" one — used for size estimates and display only. All images
in the list get pulled.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppInfo:
    key: str               # internal identifier, used in URLs and config
    name: str              # friendly name shown in the wizard
    description: str       # one-sentence summary
    images: list[str]      # docker image refs (primary first, then deps)
    approx_mb: int         # rough size for UI hint, sum of images


# Pinned to versions in Nomad's service_seeder.js as of admin image
# v1.31.0. If a future Nomad release wants different versions, update
# both here AND verify with `verify-template` against the new ISO.
OPTIONAL_APPS: list[AppInfo] = [
    AppInfo(
        key="kiwix",
        name="Information Library",
        description="Offline Wikipedia, medical references, ebooks, "
                    "and how-to guides via Kiwix.",
        images=["ghcr.io/kiwix/kiwix-serve:3.8.1"],
        approx_mb=150,
    ),
    AppInfo(
        key="kolibri",
        name="Education Platform",
        description="Interactive learning platform with K-12 curriculum, "
                    "Khan Academy, and progress tracking.",
        images=["treehouses/kolibri:0.12.8"],
        approx_mb=2000,
    ),
    AppInfo(
        key="ai",
        name="AI Assistant",
        description="Local LLM chat via Ollama with semantic search "
                    "powered by the Qdrant vector database.",
        # Ollama depends on Qdrant per service_seeder.js, so picking
        # "AI Assistant" pulls both. The order matters cosmetically
        # — Ollama is the user-facing one.
        images=[
            "ollama/ollama:0.18.1",
            "qdrant/qdrant:v1.16",
        ],
        approx_mb=600,
    ),
    AppInfo(
        key="cyberchef",
        name="Data Tools",
        description="Swiss army knife for encoding, encryption, "
                    "hashing, and data analysis (CyberChef).",
        images=["ghcr.io/gchq/cyberchef:10.22.1"],
        approx_mb=80,
    ),
    AppInfo(
        key="flatnotes",
        name="Notes",
        description="Simple Markdown note-taking with local storage.",
        images=["dullage/flatnotes:v5.5.4"],
        approx_mb=50,
    ),
    # Maps is intentionally not in this list — Nomad serves maps
    # directly from the admin container (via the pmtiles npm dep)
    # rather than as a separate Docker service. No image to pull.
]


def app_by_key(key: str) -> AppInfo:
    """Look up an app by its key. Raises KeyError if not found."""
    for app in OPTIONAL_APPS:
        if app.key == key:
            return app
    raise KeyError(f"unknown app key: {key}")


def images_for_apps(keys: list[str]) -> list[str]:
    """Flatten the image lists for a set of selected app keys.

    Returns a deduplicated list preserving order — the same image
    won't be pulled twice if two apps happen to share one (none do
    today, but we don't want to break if that changes).
    """
    seen: set[str] = set()
    out: list[str] = []
    for key in keys:
        for img in app_by_key(key).images:
            if img not in seen:
                seen.add(img)
                out.append(img)
    return out


def total_size_mb(keys: list[str]) -> int:
    """Sum the approx_mb across selected apps. UI hint only."""
    return sum(app_by_key(k).approx_mb for k in keys)
