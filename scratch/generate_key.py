"""
Script para generar una clave de encriptación válida para FIELD_ENCRYPTION_KEY.
Se usa cryptography.fernet para generar un 32-byte base64-encoded key.
"""
from cryptography.fernet import Fernet

def generate():
    key = Fernet.generate_key().decode()
    print("\n" + "="*50)
    print("NUEVA CLAVE DE ENCRIPTACIÓN GENERADA")
    print("="*50)
    print(f"\nFIELD_ENCRYPTION_KEY={key}\n")
    print("Copia esta línea en tu archivo .env")
    print("="*50 + "\n")

if __name__ == "__main__":
    generate()
