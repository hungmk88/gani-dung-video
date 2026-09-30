"""Use case: plan.json + scenes.json + cached CLIP vectors + user note → edit/candidates.json (4b).

Frame vectors come from the phase 3c cache (edit/clip/<clip>.npz), so picking never decodes
video. CLIP itself is loaded only when the note names a dictionary dish whose English text
has not been embedded yet; those text vectors are cached in edit/clip/visual_text.npz.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import ValidationError

from gani_dung_video.domain.pick import (
    Candidate,
    PickFile,
    dish_matches,
    eligible_candidates,
    find_dishes,
    rank_plan,
    similar_id_pairs,
)
from gani_dung_video.domain.plan import Plan
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.scenes import ScenesFile
from gani_dung_video.domain.semantics import Vectors
from gani_dung_video.infrastructure.clip_model import load_clip_backend
from gani_dung_video.infrastructure.config_files import (
    ConfigError,
    GlossaryFile,
    load_glossary,
    load_rules,
    read_options_file,
)
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR

logger = logging.getLogger(__name__)
PLAN_FILE, SCENES_FILE, OUT_FILE = "plan.json", "scenes.json", "candidates.json"
CLIP_DIR, TEXT_CACHE = "clip", "visual_text.npz"
TextEmbedder = Callable[[Sequence[str]], Vectors]


def _read(path: Path, model: type[Plan] | type[ScenesFile], missing: str) -> Any:
    if not path.is_file():
        raise ConfigError(missing)
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as e:
        raise ConfigError(f"{path.name} hỏng — chạy lại lệnh tạo ra nó.") from e


def _frame_vectors(folder: Path, cands: Sequence[Candidate]) -> dict[str, Vectors]:
    """All cached 3c frame vectors inside each candidate segment (rows), from edit/clip/.

    Every frame counts, not only the action peak: at the peak a hand or a running machine
    often hides the product that is plainly visible a second later.
    """
    out: dict[str, Vectors] = {}
    by_clip: dict[str, list[Candidate]] = {}
    for c in cands:
        by_clip.setdefault(c.clip, []).append(c)
    for clip, group in by_clip.items():
        cache = folder / EDIT_DIR / CLIP_DIR / f"{clip}.npz"
        if not cache.is_file():
            continue
        with np.load(cache) as z:
            times, vecs = np.asarray(z["times"]), np.asarray(z["vecs"], dtype=np.float32)
        for c in group:
            inside = (times >= c.start - 1e-6) & (times <= c.end + 1e-6)
            if inside.any():
                out[c.id] = vecs[inside]
    return out


def _dish_vectors(
    folder: Path, dishes: Sequence[str], glossary: GlossaryFile, model_name: str,
    embed_texts: TextEmbedder | None, rules: Rules,
) -> dict[str, Vectors]:  # fmt: skip
    """Normalised mean text vector per dish, cached by model + exact prompt text."""
    prompts = [p for d in dishes for p in glossary.visual[d]]
    key = hashlib.sha256("\n".join([model_name, *prompts]).encode()).hexdigest()
    cache = folder / EDIT_DIR / CLIP_DIR / TEXT_CACHE
    vecs: Vectors | None = None
    if cache.is_file():
        with np.load(cache) as z:
            if str(z["key"]) == key:
                vecs = np.asarray(z["vecs"], dtype=np.float32)
    if vecs is None:
        embed = embed_texts or load_clip_backend(rules["semantic"]).embed_texts
        vecs = np.asarray(embed(prompts), dtype=np.float32)
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(cache.name + ".tmp.npz")
        np.savez(tmp, key=np.array(key), vecs=vecs)
        os.replace(tmp, cache)
    out, k = {}, 0
    for d in dishes:
        m = vecs[k : k + len(glossary.visual[d])].mean(axis=0)
        out[d] = (m / (np.linalg.norm(m) or 1.0)).astype(np.float32)
        k += len(glossary.visual[d])
    return out


def build_pick(
    options_file: Path, folder: Path, rules: Rules | None = None,
    glossary: GlossaryFile | None = None, embed_texts: TextEmbedder | None = None,
) -> tuple[PickFile, Path]:  # fmt: skip
    """Rank the candidate segments of every plan block into <folder>/edit/candidates.json.

    Raises:
        ConfigError: plan.json / scenes.json missing, corrupt, or plan built for other options.
        SemanticError: a dish needs CLIP text vectors and CLIP cannot be loaded.
    """
    rules = rules if rules is not None else load_rules()
    glossary = glossary if glossary is not None else load_glossary()
    folder = folder.expanduser().resolve()
    msgs, cfg = rules["input_msgs"], rules["pick"]
    plan: Plan = _read(folder / EDIT_DIR / PLAN_FILE, Plan, msgs["PICK_NO_PLAN"])
    scenes: ScenesFile = _read(folder / EDIT_DIR / SCENES_FILE, ScenesFile, msgs["PLAN_NO_SCENES"])
    raw = read_options_file(options_file.expanduser().resolve())
    opts: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    style = str(plan.options["style"])
    if opts.get("phong_cach") != style:
        raise ConfigError(msgs["PICK_STALE_PLAN"].format(plan=style, opt=opts.get("phong_cach")))

    allowed = plan.beats_per_scene_allowed
    pool = eligible_candidates(scenes.scenes, style, plan.beat_sec, allowed)
    dishes = find_dishes(opts.get("ghi_chu"), list(glossary.visual))
    hits: dict[str, list[str]] = {}
    if dishes and pool:
        sem = rules["semantic"]
        model = f"{sem['clip_model']}/{sem['clip_pretrained']}"
        dvec = _dish_vectors(folder, dishes, glossary, model, embed_texts, rules)
        hits = dish_matches(_frame_vectors(folder, pool), dvec, float(cfg["dish_min_similarity"]))
        pool = [c.model_copy(update={"dishes": hits.get(c.id, [])}) for c in pool]
    blocks, missing_tags = rank_plan(plan.blocks, pool, set(hits), allowed, cfg)
    result = PickFile(
        rules_version=str(rules["rules_version"]), style=style, beat_sec=plan.beat_sec,
        beats_per_scene_allowed=allowed, dishes=dishes, blocks=blocks,
        missing_shots=[str(cfg["tag_names"].get(t, t)) for t in missing_tags],
        similar_pairs=similar_id_pairs(pool, scenes.similar),
    )  # fmt: skip
    path = folder / EDIT_DIR / OUT_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return result, path
