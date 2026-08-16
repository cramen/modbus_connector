"""Каталог шаблонов устройств: JSON-шаблоны сессий внутри пакета.

Шаблоны лежат в ``modbus_connector/templates/<Manufacturer>/<Device>.json``
(package data). Чистый Python, без Qt — чтение через importlib.resources,
работает и из PyInstaller-бандла (sys._MEIPASS, файлы лежат на диске).
"""

import json
import logging
from dataclasses import dataclass
from importlib import resources
from typing import Any

log = logging.getLogger(__name__)

PACKAGE = "modbus_connector.templates"


@dataclass(frozen=True)
class TemplateInfo:
    """Один шаблон каталога; manufacturer — имя подкаталога."""

    name: str
    manufacturer: str
    resource: str  # путь внутри templates/, например "Eastron/SDM120.json"
    description: str = ""


def parse_template(text: str, source: str = "<template>") -> dict[str, Any] | None:
    """Разобрать JSON-шаблон; битые файлы — warning в лог и None, без исключений."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("skipping template %s: invalid JSON (%s)", source, exc)
        return None
    if not isinstance(data, dict):
        log.warning("skipping template %s: top level is not an object", source)
        return None
    if not isinstance(data.get("name"), str) or not data["name"]:
        log.warning("skipping template %s: missing 'name'", source)
        return None
    if not isinstance(data.get("connection"), dict):
        log.warning("skipping template %s: missing 'connection' object", source)
        return None
    if not isinstance(data.get("registers"), list):
        log.warning("skipping template %s: 'registers' is not a list", source)
        return None
    return data


def list_templates() -> list[TemplateInfo]:
    """Все шаблоны каталога, сортировка по производителю, затем по имени."""
    found: list[TemplateInfo] = []
    root = resources.files(PACKAGE)
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        manufacturer = entry.name
        for file in sorted(entry.iterdir()):
            if not file.name.endswith(".json"):
                continue
            resource_name = f"{manufacturer}/{file.name}"
            try:
                text = file.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("skipping template %s: unreadable (%s)", resource_name, exc)
                continue
            data = parse_template(text, resource_name)
            if data is None:
                continue
            found.append(
                TemplateInfo(
                    name=data["name"],
                    manufacturer=manufacturer,
                    resource=resource_name,
                    description=str(data.get("description") or ""),
                )
            )
    found.sort(key=lambda info: (info.manufacturer.lower(), info.name.lower()))
    return found


def load_template(template: TemplateInfo | str) -> dict[str, Any]:
    """Загрузить шаблон по TemplateInfo или ключу "Eastron/SDM120[.json]".

    Возвращает dict, пригодный для SessionWidget.set_state (секции
    scanner/logging/registers_options опциональны — set_state их терпит).
    Невалидный шаблон — ValueError.
    """
    resource_name = template.resource if isinstance(template, TemplateInfo) else template
    if not resource_name.endswith(".json"):
        resource_name += ".json"
    manufacturer, _, filename = resource_name.partition("/")
    if not manufacturer or not filename:
        raise ValueError(f"invalid template key: {resource_name!r}")
    entry = resources.files(PACKAGE).joinpath(manufacturer).joinpath(filename)
    try:
        text = entry.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"template not found: {resource_name}") from exc
    data = parse_template(text, resource_name)
    if data is None:
        raise ValueError(f"invalid template: {resource_name}")
    return data
