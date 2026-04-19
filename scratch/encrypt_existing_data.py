"""
Script de utilidad para encriptar tokens existentes en texto plano.
Se debe ejecutar DESPUÉS de configurar FIELD_ENCRYPTION_KEY en el entorno.
Para correrlo: python manage.py shell < scratch/encrypt_existing_data.py
"""
import os
import django

# Configurar el entorno de Django si se corre como script independiente
if __name__ == "__main__":
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()

from apps.core.models import BusinessConfig

def migrate_to_encrypted():
    print("Starting data encryption migration...")
    configs = BusinessConfig.objects.all()
    count = 0
    
    for config in configs:
        # El campo EncryptedCharField detecta automáticamente si el valor
        # es texto plano y lo encripta en el método get_prep_value al llamar a .save()
        # Solo actualizamos los campos sensibles.
        config.save(update_fields=['whatsapp_token', 'whatsapp_app_secret'])
        count += 1
        print(f"  - Configuración del negocio '{config.business.name}' protegida.")

    print(f"\n✅ Proceso finalizado. {count} configuraciones fueron encriptadas.")

if __name__ == "__main__":
    migrate_to_encrypted()
