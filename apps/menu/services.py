"""
Servicio del menú interactivo.
Genera textos del menú y procesa selecciones del usuario.
"""
from apps.menu.models import MenuCategory, MenuSubcategory, MenuSubSubcategory


class MenuService:
    """Gestiona la lógica del menú interactivo por números."""

    @staticmethod
    def get_greeting_with_menu(business):
        """
        Genera el mensaje de saludo + menú principal.
        Si hay greeting_message personalizado, lo usa. Si no, genera uno automático.
        """
        config = business.config
        categories = MenuCategory.objects.filter(
            business=business, is_active=True
        ).order_by('order')

        if not categories.exists():
            # Sin menú, directo a la IA
            greeting = config.greeting_message or f'¡Hola! 👋 Bienvenido/a a {business.name}. ¿En qué te puedo ayudar?'
            return greeting, False  # False = no menú

        # Construir menú
        greeting = config.greeting_message or f'¡Hola! 👋 Bienvenido/a a *{business.name}*.'
        menu_text = f'{greeting}\n\n¿En qué te puedo ayudar?\n\n'

        for i, cat in enumerate(categories, 1):
            emoji = cat.emoji or f'{i}️⃣'
            menu_text += f'{emoji} *{i}.* {cat.name}'
            if cat.description:
                menu_text += f' - {cat.description}'
            menu_text += '\n'

        menu_text += '\n📝 _Escribí el número de la opción._'
        return menu_text, True  # True = hay menú

    @staticmethod
    def get_menu_only(business):
        """
        Genera solo las opciones del menú principal, sin saludo.
        Para cuando el usuario pide ver el menú durante una conversación.
        """
        categories = MenuCategory.objects.filter(
            business=business, is_active=True
        ).order_by('order')

        if not categories.exists():
            return None, False

        menu_text = '📋 *Menú de opciones:*\n\n'
        for i, cat in enumerate(categories, 1):
            emoji = cat.emoji or f'{i}️⃣'
            menu_text += f'{emoji} *{i}.* {cat.name}'
            if cat.description:
                menu_text += f' - {cat.description}'
            menu_text += '\n'

        menu_text += '\n📝 _Escribí el número de la opción._'
        return menu_text, True

    @staticmethod
    def get_submenu_text(category):
        """Genera el texto del sub-menú de una categoría."""
        subcategories = MenuSubcategory.objects.filter(
            category=category, is_active=True
        ).order_by('order')

        if not subcategories.exists():
            return None  # No hay subcategorías, ir directo a IA

        emoji = category.emoji or '📋'
        text = f'{emoji} *{category.name}* - Elegí una opción:\n\n'

        for i, sub in enumerate(subcategories, 1):
            sub_emoji = sub.emoji or f'{i}️⃣'
            text += f'{sub_emoji} *{i}.* {sub.name}'
            if sub.description:
                text += f' - {sub.description}'
            text += '\n'

        text += '\n*0.* 🔙 Volver al menú principal'
        text += '\n\n📝 _Escribí el número de la opción._'
        return text

    @staticmethod
    def get_sub_submenu_text(subcategory):
        """Genera el texto del sub-sub-menú de una subcategoría."""
        children = MenuSubSubcategory.objects.filter(
            subcategory=subcategory, is_active=True
        ).order_by('order')

        if not children.exists():
            return None

        emoji = subcategory.emoji or '📋'
        text = f'{emoji} *{subcategory.name}* - Elegí una opción:\n\n'

        for i, child in enumerate(children, 1):
            child_emoji = child.emoji or f'{i}️⃣'
            text += f'{child_emoji} *{i}.* {child.name}'
            if child.description:
                text += f' - {child.description}'
            text += '\n'

        text += '\n*0.* 🔙 Volver atrás'
        text += '\n\n📝 _Escribí el número de la opción._'
        return text

    # ------------------------------------------------------------------ #
    #  Generadores de Interactive List Messages para WhatsApp             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_greeting_interactive_list(business):
        """
        Genera los datos para un Interactive List Message del menú principal
        con saludo. Devuelve (interactive_data, has_menu).
        interactive_data es un dict con: body_text, button_text, sections,
        header_text, footer_text (o None si no hay menú).
        """
        config = business.config
        categories = MenuCategory.objects.filter(
            business=business, is_active=True
        ).order_by('order')

        if not categories.exists():
            return None, False

        greeting = config.greeting_message or f'¡Hola! 👋 Bienvenido/a a *{business.name}*.'
        body_text = f'{greeting}\n\n¿En qué te puedo ayudar?'

        rows = []
        for i, cat in enumerate(categories, 1):
            row = {
                'id': f'main_{i}',
                'title': f'{cat.emoji} {cat.name}'[:24] if cat.emoji else cat.name[:24],
            }
            if cat.description:
                row['description'] = cat.description[:72]
            rows.append(row)

        sections = [{'title': 'Opciones', 'rows': rows}]

        return {
            'body_text': body_text,
            'button_text': 'Ver opciones',
            'sections': sections,
            'header_text': business.name[:60],
            'footer_text': 'Seleccioná una opción',
        }, True

    @staticmethod
    def get_menu_only_interactive_list(business):
        """
        Genera solo las opciones del menú principal como Interactive List,
        sin saludo. Para cuando el usuario pide ver el menú explícitamente.
        """
        categories = MenuCategory.objects.filter(
            business=business, is_active=True
        ).order_by('order')

        if not categories.exists():
            return None, False

        rows = []
        for i, cat in enumerate(categories, 1):
            row = {
                'id': f'main_{i}',
                'title': f'{cat.emoji} {cat.name}'[:24] if cat.emoji else cat.name[:24],
            }
            if cat.description:
                row['description'] = cat.description[:72]
            rows.append(row)

        sections = [{'title': 'Opciones', 'rows': rows}]

        return {
            'body_text': '📋 *Menú de opciones:*\n\nElegí la opción que necesitás.',
            'button_text': 'Ver opciones',
            'sections': sections,
            'header_text': None,
            'footer_text': 'Seleccioná una opción',
        }, True

    @staticmethod
    def get_submenu_interactive_list(category):
        """Genera los datos para un Interactive List del sub-menú de una categoría."""
        subcategories = MenuSubcategory.objects.filter(
            category=category, is_active=True
        ).order_by('order')

        if not subcategories.exists():
            return None

        rows = []
        for i, sub in enumerate(subcategories, 1):
            row = {
                'id': f'sub_{i}',
                'title': f'{sub.emoji} {sub.name}'[:24] if sub.emoji else sub.name[:24],
            }
            if sub.description:
                row['description'] = sub.description[:72]
            rows.append(row)

        # Agregar opción de volver
        rows.append({
            'id': 'back_main',
            'title': '🔙 Volver al menú',
        })

        emoji = category.emoji or '📋'
        sections = [{'title': category.name[:24], 'rows': rows}]

        return {
            'body_text': f'{emoji} *{category.name}*\n\nElegí una opción:',
            'button_text': 'Ver opciones',
            'sections': sections,
            'header_text': category.name[:60],
            'footer_text': 'Seleccioná una opción',
        }

    @staticmethod
    def get_sub_submenu_interactive_list(subcategory):
        """Genera los datos para un Interactive List del sub-sub-menú."""
        children = MenuSubSubcategory.objects.filter(
            subcategory=subcategory, is_active=True
        ).order_by('order')

        if not children.exists():
            return None

        rows = []
        for i, child in enumerate(children, 1):
            row = {
                'id': f'subsub_{i}',
                'title': f'{child.emoji} {child.name}'[:24] if child.emoji else child.name[:24],
            }
            if child.description:
                row['description'] = child.description[:72]
            rows.append(row)

        # Agregar opción de volver
        rows.append({
            'id': 'back_submenu',
            'title': '🔙 Volver atrás',
        })

        emoji = subcategory.emoji or '📋'
        sections = [{'title': subcategory.name[:24], 'rows': rows}]

        return {
            'body_text': f'{emoji} *{subcategory.name}*\n\nElegí una opción:',
            'button_text': 'Ver opciones',
            'sections': sections,
            'header_text': subcategory.name[:60],
            'footer_text': 'Seleccioná una opción',
        }

    @staticmethod
    def get_menu_response_nav_interactive_list(response_text, has_subcategory=False):
        """
        Genera un Interactive List con opciones de navegación después de
        una auto_response. Incluye 'Volver atrás' y 'Menú principal'.
        """
        rows = [
            {
                'id': 'back_nav',
                'title': '🔙 Volver atrás',
            },
            {
                'id': 'back_main_nav',
                'title': '🏠 Menú principal',
            },
        ]

        sections = [{'title': 'Navegación', 'rows': rows}]

        return {
            'body_text': response_text[:4096],
            'button_text': 'Opciones',
            'sections': sections,
            'header_text': None,
            'footer_text': 'Elegí una opción o escribí tu consulta',
        }

    @staticmethod
    def process_main_menu_selection(business, selection_number):
        """
        Procesa la selección del menú principal.
        Devuelve (category, response_text, next_state).
        """
        categories = list(
            MenuCategory.objects.filter(
                business=business, is_active=True
            ).order_by('order')
        )

        if not 1 <= selection_number <= len(categories):
            # Número inválido
            menu_text, _ = MenuService.get_greeting_with_menu(business)
            return None, f'⚠️ Opción no válida. Intentá de nuevo:\n\n{menu_text}', 'main_menu'

        category = categories[selection_number - 1]
        submenu_text = MenuService.get_submenu_text(category)

        if submenu_text:
            return category, submenu_text, 'submenu'
        else:
            # Sin subcategorías, ir directo a IA con contexto de la categoría
            return category, None, 'ai_chat'

    @staticmethod
    def _append_menu_nav_hint(text):
        """Agrega opciones de navegación al final de una respuesta de menú."""
        hint = '\n\n──────────────\n📋 *0.* Volver atrás\n🏠 *00.* Volver al menú principal'
        return text + hint

    @staticmethod
    def process_submenu_selection(category, selection_number):
        """
        Procesa la selección de subcategoría.
        Devuelve (subcategory, response_text, next_state).
        """
        if selection_number == 0:
            # Volver al menú principal
            return None, None, 'back_to_main'

        subcategories = list(
            MenuSubcategory.objects.filter(
                category=category, is_active=True
            ).order_by('order')
        )

        if not 1 <= selection_number <= len(subcategories):
            submenu_text = MenuService.get_submenu_text(category)
            return None, f'⚠️ Opción no válida. Intentá de nuevo:\n\n{submenu_text}', 'submenu'

        subcategory = subcategories[selection_number - 1]

        # Verificar si tiene sub-subcategorías
        sub_submenu_text = MenuService.get_sub_submenu_text(subcategory)
        if sub_submenu_text:
            return subcategory, sub_submenu_text, 'sub_submenu'

        if subcategory.auto_response:
            response = MenuService._append_menu_nav_hint(subcategory.auto_response)
            return subcategory, response, 'menu_response'
        else:
            return subcategory, None, 'ai_chat'

    @staticmethod
    def process_sub_submenu_selection(subcategory, selection_number):
        """
        Procesa la selección de sub-subcategoría (3er nivel).
        Devuelve (sub_subcategory, response_text, next_state).
        """
        if selection_number == 0:
            # Volver al submenu padre
            return None, None, 'back_to_submenu'

        children = list(
            MenuSubSubcategory.objects.filter(
                subcategory=subcategory, is_active=True
            ).order_by('order')
        )

        if not 1 <= selection_number <= len(children):
            sub_submenu_text = MenuService.get_sub_submenu_text(subcategory)
            return None, f'⚠️ Opción no válida. Intentá de nuevo:\n\n{sub_submenu_text}', 'sub_submenu'

        child = children[selection_number - 1]

        if child.auto_response:
            response = MenuService._append_menu_nav_hint(child.auto_response)
            return child, response, 'menu_response'
        else:
            return child, None, 'ai_chat'

    @staticmethod
    def get_full_menu_tree_text(business):
        """Retorna el árbol completo del menú como texto para contexto de la IA."""
        categories = MenuCategory.objects.filter(
            business=business, is_active=True
        ).prefetch_related(
            'subcategories', 'subcategories__children'
        ).order_by('order')

        if not categories.exists():
            return ''

        lines = []
        for cat in categories:
            lines.append(f'- {cat.emoji} {cat.name}: {cat.description}' if cat.description else f'- {cat.emoji} {cat.name}')
            for sub in cat.subcategories.filter(is_active=True).order_by('order'):
                lines.append(f'  - {sub.emoji} {sub.name}: {sub.description}' if sub.description else f'  - {sub.emoji} {sub.name}')
                if sub.auto_response:
                    lines.append(f'    (Auto-respuesta: {sub.auto_response})')
                for subsub in sub.children.filter(is_active=True).order_by('order'):
                    lines.append(f'    - {subsub.emoji} {subsub.name}: {subsub.description}' if subsub.description else f'    - {subsub.emoji} {subsub.name}')
        return '\n'.join(lines)
