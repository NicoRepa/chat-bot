from django.contrib import admin
from .models import MenuCategory, MenuSubcategory, MenuSubSubcategory


class MenuSubSubcategoryInline(admin.TabularInline):
    model = MenuSubSubcategory
    extra = 1
    fields = ('name', 'emoji', 'description', 'auto_response', 'order', 'is_active')


class MenuSubcategoryInline(admin.TabularInline):
    model = MenuSubcategory
    extra = 1
    fields = ('name', 'emoji', 'description', 'auto_response', 'order', 'is_active')
    show_change_link = True


@admin.register(MenuCategory)
class MenuCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'emoji', 'business', 'order', 'is_active')
    list_filter = ('business', 'is_active')
    list_editable = ('order', 'is_active')
    inlines = [MenuSubcategoryInline]


@admin.register(MenuSubcategory)
class MenuSubcategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'emoji', 'order', 'is_active')
    list_filter = ('category__business', 'is_active')
    list_editable = ('order', 'is_active')
    inlines = [MenuSubSubcategoryInline]
