# SPDX-License-Identifier: Apache-2.0
"""Salted per-export pseudonyms and text scrubbing for the diagnostics export.

One :class:`Pseudonymizer` lives for one export. Its random salt is never written out, so a
pseudonym cannot be matched against a guessed id and two exports do not link. Inside one export
the same real value always gets the same pseudonym, in the configuration, the decisions and the
logs alike. A device's id and its name share one pseudonym.

:meth:`Pseudonymizer.text` is the single scrubber for free text (log lines, reason arguments):
registered secrets and coordinates become ``***``; known ids, device names and area names, and
anything shaped like a Home Assistant entity id (``<domain>.<object_id>``), become pseudonyms.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets as _secrets
from collections.abc import Iterable
from typing import Final

from suncore.ha.redact import DEFAULT_REGISTRY, MASK, SecretRegistry

MIN_LITERAL_LEN: Final = 3
"""Shorter names are not searched for in free text (they would corrupt ordinary words)."""
PSEUDONYM_HEX: Final = 8

ENTITY_DOMAINS: Final = (
    "alarm_control_panel|automation|binary_sensor|button|calendar|camera|climate|conversation|"
    "counter|cover|date|datetime|device_tracker|event|fan|group|humidifier|image|input_boolean|"
    "input_button|input_datetime|input_number|input_select|input_text|lawn_mower|light|lock|"
    "media_player|notify|number|person|remote|scene|schedule|script|select|sensor|siren|sun|"
    "switch|text|time|timer|todo|tts|update|vacuum|valve|water_heater|weather|zone"
)
"""Home Assistant entity domains; a ``<domain>.<id>`` match is an entity id. A closed list on
purpose: a generic ``a.b`` pattern would also hit log events, module names and reason keys."""
_ENTITY = rf"(?:{ENTITY_DOMAINS})\.[a-z0-9][a-z0-9_]*"
_LEFT = r"(?<![A-Za-z0-9_.])"
_RIGHT = r"(?![A-Za-z0-9_])"


class Pseudonymizer:
    """Maps real ids and names to stable, salted, non-reversible pseudonyms."""

    def __init__(
        self,
        salt: bytes | None = None,
        *,
        secrets: Iterable[str] = (),
        coordinates: Iterable[float] = (),
        registry: SecretRegistry = DEFAULT_REGISTRY,
    ) -> None:
        self._salt = salt if salt is not None else _secrets.token_bytes(16)
        self._registry = registry
        self._literals: dict[str, str] = {}
        self._secrets: set[str] = {s for s in secrets if len(s) >= MIN_LITERAL_LEN + 1}
        for value in coordinates:
            self._secrets.update(_number_forms(value))
        self._pattern: re.Pattern[str] | None = None
        self._secret_pattern: re.Pattern[str] | None = None

    # --- pseudonyms -------------------------------------------------------------

    def _hash(self, kind: str, value: str) -> str:
        digest = hmac.new(self._salt, f"{kind}\0{value}".encode(), hashlib.sha256)
        return digest.hexdigest()[:PSEUDONYM_HEX]

    def entity(self, entity_id: str) -> str:
        """``sensor.sim_pv_power`` becomes ``sensor.e_1a2b3c4d`` (the domain is kept)."""
        domain, sep, _rest = entity_id.partition(".")
        if not sep or not re.fullmatch(ENTITY_DOMAINS, domain):
            return f"entity_{self._hash('entity', entity_id)}"
        return f"{domain}.e_{self._hash('entity', entity_id)}"

    def device(self, device_id: str) -> str:
        """The pseudonym of a configured device (also used for its name)."""
        return f"device_{self._hash('device', device_id)}"

    def ha_device(self, name: str) -> str:
        return f"ha_device_{self._hash('ha_device', name)}"

    def area(self, name: str) -> str:
        return f"area_{self._hash('area', name)}"

    def opaque(self, value: str, prefix: str = "ref") -> str:
        """A pseudonym for a value without a better class (an entry id, a free name)."""
        known = self._literals.get(value)
        return known if known is not None else f"{prefix}_{self._hash(prefix, value)}"

    # --- registering what text may contain ---------------------------------------

    def _register(self, real: str, pseudonym: str) -> None:
        """Keep the first registration: a configured device wins over an HA device or area of the
        same name (they are the same thing to a reader of the export)."""
        if len(real) >= MIN_LITERAL_LEN and real != pseudonym and real not in self._literals:
            self._literals[real] = pseudonym
            self._pattern = None

    def register_literal(self, real: str, pseudonym: str) -> None:
        """Replace ``real`` by ``pseudonym`` wherever it appears in free text."""
        self._register(real, pseudonym)

    def register_entity(self, entity_id: str) -> str:
        pseudonym = self.entity(entity_id)
        self._register(entity_id, pseudonym)
        return pseudonym

    def register_device(self, device_id: str, name: str | None = None) -> str:
        """Register a configured device: its id and its name get the same pseudonym."""
        pseudonym = self.device(device_id)
        self._register(device_id, pseudonym)
        if name:
            self._register(name, pseudonym)
        return pseudonym

    def register_ha_device(self, name: str) -> str:
        self._register(name, self.ha_device(name))
        return self._literals.get(name, self.ha_device(name))

    def register_area(self, name: str) -> str:
        self._register(name, self.area(name))
        return self._literals.get(name, self.area(name))

    def register_secret(self, value: str) -> None:
        if len(value) >= MIN_LITERAL_LEN + 1:
            self._secrets.add(value)
            self._secret_pattern = None

    def known_pseudonym(self, value: str) -> str | None:
        return self._literals.get(value)

    # --- free text --------------------------------------------------------------

    def text(self, value: str) -> str:
        """``value`` with secrets masked and every id, name and area pseudonymised."""
        masked = self._mask_secrets(self._registry.redact(value))
        return self._scrub(masked)

    def _mask_secrets(self, value: str) -> str:
        if not self._secrets:
            return value
        if self._secret_pattern is None:
            ordered = sorted(self._secrets, key=len, reverse=True)
            alternatives = "|".join(re.escape(s) for s in ordered)
            # No word boundaries: over-masking is the safe direction for a secret.
            self._secret_pattern = re.compile(alternatives)
        return self._secret_pattern.sub(MASK, value)

    def _scrub(self, value: str) -> str:
        if self._pattern is None:
            ordered = sorted(self._literals, key=len, reverse=True)
            literal = "|".join(re.escape(s) for s in ordered)
            body = f"(?P<lit>{literal})|(?P<ent>{_ENTITY})" if literal else f"(?P<ent>{_ENTITY})"
            self._pattern = re.compile(f"{_LEFT}(?:{body}){_RIGHT}", re.IGNORECASE)
        return self._pattern.sub(self._replace, value)

    def _replace(self, match: re.Match[str]) -> str:
        found = match.group(0)
        if match.groupdict().get("lit") is not None:
            return self._literals.get(found) or self._casefold_literal(found)
        lowered = found.lower()
        return self._literals.get(lowered) or self.entity(lowered)

    def _casefold_literal(self, found: str) -> str:
        wanted = found.casefold()
        for real, pseudonym in self._literals.items():
            if real.casefold() == wanted:
                return pseudonym
        return found  # pragma: no cover - the pattern only matches registered literals


def _number_forms(value: float) -> set[str]:
    """The ways a coordinate can appear in text: ``repr`` and fixed precisions 2 to 6."""
    forms = {repr(float(value)), str(value)}
    forms.update(f"{value:.{digits}f}" for digits in range(2, 7))
    return {f for f in forms if len(f) >= MIN_LITERAL_LEN + 1}
