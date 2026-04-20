import uuid
from django.db import models
from apps.core.models import Business


class MenuCategory(models.Model):
    """
    Categoría principal del menú interactivo.
    El cliente puede crear/editar/eliminar categorías desde el panel.
    Ejemplo: "Turnos", "Pagos", "Consultas"
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business, on_delete=models.CASCADE,
        related_name='menu_categories', verbose_name='Negocio'
    )
    name = models.CharField('Nombre', max_length=100)
    description = models.TextField('Descripción', blank=True)
    emoji = models.CharField('Emoji', max_length=10, blank=True, help_text='Emoji para el menú')
    order = models.IntegerField('Orden', default=0)
    is_active = models.BooleanField('Activa', default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Categoría del menú'
        verbose_name_plural = 'Categorías del menú'
        ordering = ['order', 'name']

    def __str__(self):
        return f'{self.emoji} {self.name}' if self.emoji else self.name


class MenuSubcategory(models.Model):
    """
    Subcategoría dentro de una categoría del menú.
    Ejemplo dentro de "Pagos": "Transferencia", "Efectivo", "Mercado Pago"
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(
        MenuCategory, on_delete=models.CASCADE,
        related_name='subcategories', verbose_name='Categoría'
    )
    name = models.CharField('Nombre', max_length=100)
    description = models.TextField('Descripción', blank=True)
    emoji = models.CharField('Emoji', max_length=10, blank=True)
    auto_response = models.TextField(
        'Respuesta automática', blank=True,
        help_text='Texto que se envía automáticamente al seleccionar esta opción. Si está vacío, la IA responde.'
    )
    order = models.IntegerField('Orden', default=0)
    is_active = models.BooleanField('Activa', default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Subcategoría del menú'
        verbose_name_plural = 'Subcategorías del menú'
        ordering = ['order', 'name']

    def __str__(self):
        return f'{self.category.name} > {self.name}'


class MenuSubSubcategory(models.Model):
    """
    Tercer nivel del menú: sub-subcategoría dentro de una subcategoría.
    Ejemplo: "Pagos" > "Transferencia" > "Banco X", "Banco Y"
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subcategory = models.ForeignKey(
        MenuSubcategory, on_delete=models.CASCADE,
        related_name='children', verbose_name='Subcategoría padre'
    )
    name = models.CharField('Nombre', max_length=100)
    description = models.TextField('Descripción', blank=True)
    emoji = models.CharField('Emoji', max_length=10, blank=True)
    auto_response = models.TextField(
        'Respuesta automática', blank=True,
        help_text='Texto que se envía automáticamente al seleccionar esta opción.'
    )
    order = models.IntegerField('Orden', default=0)
    is_active = models.BooleanField('Activa', default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Sub-subcategoría del menú'
        verbose_name_plural = 'Sub-subcategorías del menú'
        ordering = ['order', 'name']

    def __str__(self):
        return f'{self.subcategory.category.name} > {self.subcategory.name} > {self.name}'


class MenuLevel4(models.Model):
    """
    Cuarto nivel del menú: sub-sub-subcategoría.
    Ejemplo: "Pagos" > "Transferencia" > "Banco X" > "Cuentas corriente", "Caja de ahorro"
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent = models.ForeignKey(
        MenuSubSubcategory, on_delete=models.CASCADE,
        related_name='children', verbose_name='Nivel 3 padre'
    )
    name = models.CharField('Nombre', max_length=100)
    description = models.TextField('Descripción', blank=True)
    emoji = models.CharField('Emoji', max_length=10, blank=True)
    auto_response = models.TextField(
        'Respuesta automática', blank=True,
        help_text='Texto que se envía automáticamente al seleccionar esta opción.'
    )
    order = models.IntegerField('Orden', default=0)
    is_active = models.BooleanField('Activa', default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Nivel 4 del menú'
        verbose_name_plural = 'Niveles 4 del menú'
        ordering = ['order', 'name']

    def __str__(self):
        return f'{self.parent.subcategory.category.name} > {self.parent.subcategory.name} > {self.parent.name} > {self.name}'
