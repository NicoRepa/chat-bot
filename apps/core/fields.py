"""
Campo encriptado con Fernet para datos sensibles.
Encripta transparentemente al guardar y desencripta al leer.
"""
import base64
import logging

from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception
    logger.warning('cryptography no instalada. EncryptedCharField guardará en texto plano.')


def _get_fernet():
    """Obtiene una instancia de Fernet con la clave de settings."""
    key = getattr(settings, 'FIELD_ENCRYPTION_KEY', b'')
    if not key:
        return None
    if isinstance(key, str):
        key = key.encode()
    try:
        return Fernet(key)
    except Exception as exc:
        logger.warning('FIELD_ENCRYPTION_KEY inválida: %s — datos se guardan sin encriptar.', exc)
        return None


class EncryptedCharField(models.CharField):
    """
    CharField que encripta el valor con Fernet al guardarlo en la DB
    y lo desencripta transparentemente al leerlo.

    - Si FIELD_ENCRYPTION_KEY no está configurada, funciona como CharField normal.
    - Si el valor almacenado no se puede desencriptar (ej: migración pendiente),
      retorna el valor crudo con un warning en el log.
    """

    def get_prep_value(self, value):
        """Encripta el valor antes de guardarlo en la DB."""
        value = super().get_prep_value(value)
        if not value:
            return value
        f = _get_fernet()
        if f is None:
            return value
        try:
            # Si ya está encriptado (es base64 Fernet válido), no re-encriptar
            try:
                f.decrypt(value.encode())
                return value  # Ya estaba encriptado
            except Exception:
                pass
            return f.encrypt(value.encode()).decode()
        except Exception as exc:
            logger.warning('Error encriptando campo: %s', exc)
            return value

    def from_db_value(self, value, expression, connection):
        """Desencripta el valor al leerlo de la DB."""
        if not value:
            return value
        f = _get_fernet()
        if f is None:
            return value
        try:
            return f.decrypt(value.encode()).decode()
        except InvalidToken:
            logger.warning(
                'No se pudo desencriptar valor (¿migración pendiente o key incorrecta?). '
                'Se retorna el valor crudo.'
            )
            return value
        except Exception as exc:
            logger.warning('Error desencriptando campo: %s', exc)
            return value
