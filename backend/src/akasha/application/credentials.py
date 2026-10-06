"""CredentialService: the one place secrets are created, rotated and resolved
(architecture §32).

Boundaries:
- Plaintext enters as ``Secret`` and is encrypted here, before any
  repository call — persistence, SQL errors and ORM state only ever see
  ciphertext.
- There is no read-back of values: management operations return metadata;
  only ``resolve`` decrypts, and its caller is the runtime's per-node
  resolver (``akasha.runtime.credentials``), not the API.
- Audit log lines carry name and kind only.
"""

import logging
from dataclasses import replace

from akasha.application.persistence import UnitOfWork
from akasha.application.workflows import Page, UowFactory, check_page, to_page
from akasha.domain.credentials import (
    Credential,
    CredentialKind,
    CredentialReference,
    Secret,
)
from akasha.domain.primitives import Timestamps, utcnow
from akasha.errors import AkashaError
from akasha.security.crypto import SecretCipher

logger = logging.getLogger("akasha.application.credentials")


class CredentialNotFoundError(AkashaError):
    code = "CREDENTIAL_NOT_FOUND"
    http_status = 404


class CredentialAlreadyExistsError(AkashaError):
    code = "CREDENTIAL_ALREADY_EXISTS"
    http_status = 409


def _not_found(name: str) -> CredentialNotFoundError:
    return CredentialNotFoundError("Credential not found.", details={"name": name})


class CredentialService:
    def __init__(self, uow: UowFactory, cipher: SecretCipher) -> None:
        self._uow = uow
        self._cipher = cipher

    async def create(
        self,
        name: str,
        *,
        kind: CredentialKind,
        value: Secret,
        description: str = "",
    ) -> Credential:
        now = utcnow()
        credential = Credential(
            name=name,
            kind=kind,
            description=description,
            timestamps=Timestamps(created_at=now, updated_at=now),
        )
        ciphertext = self._encrypt(credential.name, value)
        async with self._uow() as uow:
            if not await uow.credentials.add(credential, ciphertext):
                raise CredentialAlreadyExistsError(
                    "A credential with this name already exists.",
                    details={"name": name},
                )
            await uow.commit()
        _log("credential_created", credential)
        return credential

    async def get(self, name: str) -> Credential:
        async with self._uow() as uow:
            return await _load(uow, name)

    async def list(self, *, offset: int = 0, limit: int = 50) -> Page[Credential]:
        check_page(offset, limit)
        async with self._uow() as uow:
            rows = await uow.credentials.list(offset=offset, limit=limit + 1)
        return to_page(rows, offset, limit)

    async def update(
        self,
        name: str,
        *,
        description: str | None = None,
        value: Secret | None = None,
    ) -> Credential:
        """Change the description and/or rotate the value. Name and kind are
        fixed: the name is what workflows reference."""
        async with self._uow() as uow:
            credential = await _load(uow, name)
            if description is not None:
                # replace() re-runs the domain validation of the new value.
                credential = replace(credential, description=description)
            credential.touch()
            ciphertext = None if value is None else self._encrypt(name, value)
            if not await uow.credentials.update(credential, ciphertext):
                raise _not_found(name)  # deleted concurrently
            await uow.commit()
        _log("credential_updated", credential, rotated=value is not None)
        return credential

    async def delete(self, name: str) -> None:
        """Workflows still referencing the name fail at resolution time with
        CREDENTIAL_NOT_FOUND; recreating the name repairs them."""
        async with self._uow() as uow:
            credential = await _load(uow, name)
            if not await uow.credentials.delete(name):
                raise _not_found(name)
            await uow.commit()
        _log("credential_deleted", credential)

    async def resolve(self, reference: str) -> Secret:
        """Decrypt the credential a reference points to. Raises
        ``DomainValidationError`` (malformed reference),
        ``CredentialNotFoundError`` or ``SecretDecryptionError``."""
        name = CredentialReference(reference).name
        async with self._uow() as uow:
            ciphertext = await uow.credentials.get_ciphertext(name)
        if ciphertext is None:
            raise _not_found(name)
        secret = Secret(self._cipher.decrypt(ciphertext, context=name))
        logger.info(
            "credential_resolved",
            extra={"event": "credential_resolved", "credential_name": name},
        )
        return secret

    def _encrypt(self, name: str, value: Secret) -> bytes:
        return self._cipher.encrypt(value.reveal(), context=name)


async def _load(uow: UnitOfWork, name: str) -> Credential:
    credential = await uow.credentials.get(name)
    if credential is None:
        raise _not_found(name)
    return credential


def _log(event: str, credential: Credential, **extra: object) -> None:
    logger.info(
        event,
        extra={
            "event": event,
            "credential_name": credential.name,
            "credential_kind": credential.kind.value,
            **extra,
        },
    )
