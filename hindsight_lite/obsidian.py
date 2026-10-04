from __future__ import annotations

import html
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .db import MemoryStore

_GENERATED_MARKER = '<!-- hindsight-lite:generated -->'
_MANIFEST = '.hindsight-lite-manifest.json'
_MAX_FILENAME = 120


def _clean_scalar(value: object, *, limit: int = 500) -> str:
    text = unicodedata.normalize('NFKC', str(value or '')).replace('\x00', '').strip()
    text = ' '.join(text.split())
    return text[:limit]


def _yaml_string(value: object) -> str:
    # JSON string syntax is valid YAML and safely quotes colons/newlines/quotes.
    return json.dumps(_clean_scalar(value, limit=2000), ensure_ascii=False)


def _safe_display(value: object) -> str:
    text = _clean_scalar(value, limit=240)
    return text.replace('[[', '［［').replace(']]', '］］').replace('|', '¦')


def _safe_stem(value: object, *, fallback: str) -> str:
    text = unicodedata.normalize('NFKC', str(value or '')).strip()
    text = re.sub(r'[\\/:*?"<>|\[\]#^]', '-', text)
    text = re.sub(r'[\x00-\x1f\x7f]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip(' .-')
    if not text:
        text = fallback
    # Avoid special path components and hidden/control filenames.
    if text in {'.', '..'} or text.startswith('.'):
        text = fallback
    return text[:_MAX_FILENAME].rstrip(' .') or fallback


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip().replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


@dataclass(frozen=True)
class ObsidianSyncReport:
    written: int
    removed: int
    skipped_removals: int
    vault: str
    unchanged: int = 0


class ObsidianMirror:
    """One-way, path-confined SQLite -> Obsidian mirror.

    The mirror owns only files listed in its private manifest. It never reads Markdown
    back into the memory database and never deletes files it cannot prove it generated.
    """

    def __init__(self, store: MemoryStore, vault_path: str | Path):
        self.store = store
        self.vault = Path(vault_path).expanduser()
        self.vault.mkdir(parents=True, exist_ok=True)
        if self.vault.is_symlink():
            raise ValueError('vault root must not be a symlink')
        self._vault_real = self.vault.resolve(strict=True)

    def _confined(self, relative: str | Path) -> Path:
        rel = Path(relative)
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('path escapes vault')
        target = self.vault / rel
        # Refuse traversal through any existing symlink parent.
        current = self.vault
        for part in rel.parts[:-1]:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ValueError('symlinked directories are not allowed in managed paths')
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = parent.resolve(strict=True)
        if resolved_parent != self._vault_real and self._vault_real not in resolved_parent.parents:
            raise ValueError('path escapes vault')
        if target.exists() and target.is_symlink():
            raise ValueError('managed file must not be a symlink')
        return target

    def _atomic_write(self, relative: str, content: str) -> bool:
        """Write a managed note. Returns False (and touches nothing) if it is already up to date."""
        target = self._confined(relative)
        if not content.startswith(_GENERATED_MARKER):
            content = _GENERATED_MARKER + '\n' + content
        if target.exists():
            try:
                existing = target.read_text(encoding='utf-8', errors='replace')
            except OSError as exc:
                raise FileExistsError(f'cannot safely inspect existing managed path: {relative}') from exc
            if _GENERATED_MARKER not in existing[:256]:
                raise FileExistsError(f'refusing to overwrite non-generated vault file: {relative}')
            if existing == content:
                return False
        fd, tmp_name = tempfile.mkstemp(prefix='.hmem-', suffix='.tmp', dir=str(target.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
            return True
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _load_manifest(self) -> set[str]:
        path = self._confined(_MANIFEST)
        if not path.exists():
            return set()
        if path.is_symlink():
            raise ValueError('manifest must not be a symlink')
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return set()
        if not isinstance(data, dict) or not isinstance(data.get('files'), list):
            return set()
        safe: set[str] = set()
        for item in data['files']:
            if not isinstance(item, str):
                continue
            rel = Path(item)
            if rel.is_absolute() or '..' in rel.parts or item == _MANIFEST:
                continue
            safe.add(rel.as_posix())
        return safe

    def _write_manifest(self, files: Iterable[str]) -> None:
        target = self._confined(_MANIFEST)
        if target.exists():
            if target.is_symlink():
                raise ValueError('manifest must not be a symlink')
            try:
                existing = json.loads(target.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as exc:
                raise FileExistsError('refusing to overwrite an unrecognized manifest file') from exc
            if not isinstance(existing, dict) or existing.get('generated_by') != 'hindsight-lite':
                raise FileExistsError('refusing to overwrite a manifest not owned by hindsight-lite')
        payload = {
            'format': 1,
            'generated_by': 'hindsight-lite',
            'files': sorted(set(files)),
        }
        # Manifest is not a Markdown page, so write it directly but atomically.
        fd, tmp_name = tempfile.mkstemp(prefix='.hmem-manifest-', suffix='.tmp', dir=str(target.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
            return True
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _entity_file(entity: dict) -> str:
        stem = _safe_stem(entity['canonical_name'], fallback='Entity')
        suffix = str(entity['id']).replace('ent_', '')[:8]
        return f'Entities/{stem}--{suffix}.md'

    @staticmethod
    def _memory_file(memory: dict) -> str:
        return f"Memories/{_safe_stem(memory['id'], fallback='memory')}.md"

    @staticmethod
    def _wiki(relative: str, label: str) -> str:
        target = Path(relative).with_suffix('').as_posix()
        return f'[[{target}|{_safe_display(label)}]]'

    def _render_entity(self, entity: dict, entity_files: dict[str, str], memory_files: dict[str, str]) -> str:
        linked_memories = self.store.memories_for_entity(entity['id'], include_inactive=True, limit=500)
        rels = self.store.neighbors(entity['id'], limit=500)
        aliases = entity.get('aliases', [])
        lines = [
            '---',
            'hindsight_lite: true',
            'kind: entity',
            f"entity_id: {_yaml_string(entity['id'])}",
            f"entity_type: {_yaml_string(entity.get('entity_type') or '')}",
            f"canonical_name: {_yaml_string(entity['canonical_name'])}",
            'aliases:',
        ]
        for alias in aliases[:100]:
            lines.append(f'  - {_yaml_string(alias)}')
        lines += ['---', '', f"# {_safe_display(entity['canonical_name'])}", '']
        if rels:
            lines += ['## Relationships', '']
            for rel in rels:
                if rel['subject_entity_id'] == entity['id']:
                    other_id, direction, other_name = rel['object_entity_id'], '→', rel['object_name']
                else:
                    other_id, direction, other_name = rel['subject_entity_id'], '←', rel['subject_name']
                other_file = entity_files.get(other_id)
                if not other_file:
                    continue
                lines.append(f"- `{_safe_display(rel['predicate'])}` {direction} {self._wiki(other_file, other_name)}")
            lines.append('')
        if linked_memories:
            lines += ['## Memories', '']
            for memory in linked_memories:
                mf = memory_files.get(memory['id'])
                if mf:
                    lines.append(f"- {self._wiki(mf, memory['content'][:90])} — `{memory['status']}`")
            lines.append('')
        return '\n'.join(lines).rstrip() + '\n'

    def _render_memory(self, memory: dict, entity_files: dict[str, str]) -> str:
        entities = self.store.entities_for_memory(memory['id'])
        relationships = self.store.relationships_for_memory(memory['id'])
        lines = [
            '---',
            'hindsight_lite: true',
            'kind: memory',
            f"memory_id: {_yaml_string(memory['id'])}",
            f"memory_type: {_yaml_string(memory['memory_type'])}",
            f"subtype: {_yaml_string(memory.get('subtype') or '')}",
            f"status: {_yaml_string(memory['status'])}",
            f"confidence: {float(memory['confidence']):.6g}",
            f"importance: {float(memory['importance']):.6g}",
            f"created_at: {_yaml_string(memory['created_at'])}",
            f"updated_at: {_yaml_string(memory['updated_at'])}",
            f"event_time: {_yaml_string(memory.get('event_time') or '')}",
            '---', '', '# Memory', '', '## Content', '',
        ]
        # Render memory content as escaped HTML preformatted text. This keeps untrusted
        # Markdown/wiki/image syntax inert when Obsidian renders the generated note.
        safe_content = html.escape(str(memory['content']).replace('\x00', ''), quote=False)
        lines.extend(['<pre>', safe_content, '</pre>', ''])
        if entities:
            lines += ['## Entities', '']
            for entity in entities:
                ef = entity_files.get(entity['id'])
                if ef:
                    lines.append(f"- {self._wiki(ef, entity['canonical_name'])} — `{_safe_display(entity['role'])}`")
            lines.append('')
        if relationships:
            lines += ['## Relationships', '']
            for rel in relationships:
                sf = entity_files.get(rel['subject_entity_id'])
                of = entity_files.get(rel['object_entity_id'])
                if sf and of:
                    lines.append(
                        f"- {self._wiki(sf, rel['subject_name'])} `--{_safe_display(rel['predicate'])}→` {self._wiki(of, rel['object_name'])}"
                    )
            lines.append('')
        if memory.get('superseded_by'):
            lines += ['## Superseded by', '', f"`{_safe_display(memory['superseded_by'])}`", '']
        return '\n'.join(lines).rstrip() + '\n'

    def _render_indexes(self, entities: list[dict], memories: list[dict], entity_files: dict[str, str], memory_files: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        home = [
            '---', 'hindsight_lite: true', 'kind: index', '---', '',
            '# Hindsight Lite Memory', '',
            f'- Entities: **{len(entities)}**',
            f'- Memories: **{len(memories)}**',
            '',
            '- [[00-Index/Entities|Entities]]',
            '- [[00-Index/Memories|Memories]]',
            '- [[00-Index/Timeline|Timeline]]',
            '',
            '> This vault is a generated one-way mirror. SQLite is authoritative.',
        ]
        out['00-Index/Home.md'] = '\n'.join(home) + '\n'

        entity_lines = ['---', 'hindsight_lite: true', 'kind: index', '---', '', '# Entities', '']
        for e in sorted(entities, key=lambda x: normalize_for_sort(x['canonical_name'])):
            entity_lines.append(f"- {self._wiki(entity_files[e['id']], e['canonical_name'])}")
        out['00-Index/Entities.md'] = '\n'.join(entity_lines) + '\n'

        memory_lines = ['---', 'hindsight_lite: true', 'kind: index', '---', '', '# Memories', '']
        for m in sorted(memories, key=lambda x: x['updated_at'], reverse=True):
            memory_lines.append(f"- {self._wiki(memory_files[m['id']], m['content'][:100])} — `{m['memory_type']}/{m.get('subtype') or '-'}` · `{m['status']}`")
        out['00-Index/Memories.md'] = '\n'.join(memory_lines) + '\n'

        timeline_groups: dict[str, list[dict]] = {}
        for m in memories:
            dt = _parse_time(m.get('event_time')) or _parse_time(m.get('created_at'))
            key = dt.date().isoformat() if dt else 'Unknown date'
            timeline_groups.setdefault(key, []).append(m)
        timeline = ['---', 'hindsight_lite: true', 'kind: index', '---', '', '# Timeline', '']
        for day in sorted(timeline_groups, reverse=True):
            timeline += [f'## {day}', '']
            for m in timeline_groups[day]:
                timeline.append(f"- {self._wiki(memory_files[m['id']], m['content'][:90])}")
            timeline.append('')
        out['00-Index/Timeline.md'] = '\n'.join(timeline).rstrip() + '\n'
        return out

    def sync(self, *, include_inactive: bool = False, prune: bool = True) -> ObsidianSyncReport:
        entities = self.store.list_entities(limit=100000)
        memories = self.store.list_memories(include_inactive=include_inactive, limit=100000)
        entity_files = {e['id']: self._entity_file(e) for e in entities}
        memory_files = {m['id']: self._memory_file(m) for m in memories}

        generated: dict[str, str] = {}
        for entity in entities:
            generated[entity_files[entity['id']]] = self._render_entity(entity, entity_files, memory_files)
        for memory in memories:
            generated[memory_files[memory['id']]] = self._render_memory(memory, entity_files)
        generated.update(self._render_indexes(entities, memories, entity_files, memory_files))

        old = self._load_manifest()
        written = 0
        for relative, content in generated.items():
            if self._atomic_write(relative, content):
                written += 1

        removed = 0
        skipped = 0
        if prune:
            for relative in sorted(old - set(generated)):
                try:
                    path = self._confined(relative)
                except ValueError:
                    skipped += 1
                    continue
                if not path.exists() or path.is_symlink() or not path.is_file():
                    skipped += 1
                    continue
                try:
                    head = path.read_text(encoding='utf-8', errors='replace')[:256]
                except OSError:
                    skipped += 1
                    continue
                if _GENERATED_MARKER not in head:
                    skipped += 1
                    continue
                path.unlink()
                removed += 1

        self._write_manifest(generated)
        return ObsidianSyncReport(
            written=written, removed=removed, skipped_removals=skipped, vault=str(self.vault),
            unchanged=len(generated) - written,
        )


def normalize_for_sort(value: object) -> str:
    return unicodedata.normalize('NFKC', str(value or '')).casefold()
