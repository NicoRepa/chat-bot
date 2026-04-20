"""
Servicio de IA con OpenAI ChatGPT.
Genera respuestas, clasifica leads y resume conversaciones.
"""
import json
import time
import logging
from django.conf import settings
from openai import OpenAI

logger = logging.getLogger(__name__)

# Constantes de retry
MAX_RETRIES = 3
BASE_RETRY_DELAY = 5  # segundos

# Costos estimados por token según modelo
_COST_TABLE = {
    'gpt-4o-mini': 0.000001,
    'gpt-3.5': 0.000001,
    'gpt-4o': 0.000005,
    'gpt-4': 0.00003,
}

def _cost_per_token(model_name):
    """Retorna el costo estimado por token según el modelo."""
    model_lower = (model_name or '').lower()
    for key, cost in _COST_TABLE.items():
        if key in model_lower:
            return cost
    return 0.000002  # default

def _usage_from_response(response, model_name):
    """Extrae uso de tokens y costo de una response de OpenAI."""
    if not response or not hasattr(response, 'usage') or not response.usage:
        return {'tokens': 0, 'cost': 0.0}
    tokens = response.usage.total_tokens
    cost = tokens * _cost_per_token(model_name)
    return {'tokens': tokens, 'cost': cost}


class ChatGPTService:
    """
    Servicio centralizado de IA con OpenAI ChatGPT.
    Genera respuestas, clasifica conversaciones y crea resúmenes.
    """

    def __init__(self):
        api_key = settings.OPENAI_API_KEY
        if api_key:
            self.client = OpenAI(api_key=api_key)
        else:
            self.client = None
            logger.warning('OpenAI API key no configurada. La IA no estará disponible.')

    def _call_with_retry(self, func, *args, **kwargs):
        """
        Ejecuta una llamada a OpenAI con retry automático para rate limits.
        Espera con backoff exponencial en caso de error 429.
        """
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_str = str(e)
                is_rate_limit = '429' in error_str or 'rate_limit' in error_str.lower()

                if is_rate_limit and attempt < MAX_RETRIES - 1:
                    delay = BASE_RETRY_DELAY * (2 ** attempt)
                    logger.warning(
                        f'Rate limit de OpenAI (intento {attempt + 1}/{MAX_RETRIES}). '
                        f'Reintentando en {delay}s...'
                    )
                    time.sleep(delay)
                else:
                    raise
        return None

    def _build_system_prompt(self, business, conversation=None):
        """
        Construye el prompt del sistema con toda la info del negocio.
        Incluye: instrucciones, knowledge base, selecciones del menú.
        """
        config = business.config
        prompt_parts = []

        # Instrucciones base
        prompt_parts.append(
            f'Sos el asistente virtual de "{business.name}", '
            f'un negocio del rubro "{business.industry}".'
        )

        # Prompt personalizado del cliente
        if config.system_prompt:
            prompt_parts.append(f'\nINSTRUCCIONES DEL NEGOCIO:\n{config.system_prompt}')

        # Knowledge base del negocio
        if config.knowledge_base:
            prompt_parts.append(
                f'\nINFORMACIÓN DEL NEGOCIO (usá esto para responder preguntas):\n'
                f'{config.knowledge_base}'
            )

        # Información de contacto
        contact_lines = []
        if business.address:
            contact_lines.append(f'- Dirección: {business.address}')
        if business.phone:
            contact_lines.append(f'- Teléfono: {business.phone}')
        if business.email:
            contact_lines.append(f'- Email: {business.email}')
            
        if contact_lines:
            prompt_parts.append(f'\nDATOS DE CONTACTO:\n' + '\n'.join(contact_lines))

        # Horarios de atención
        try:
            from apps.core.schedule_utils import get_schedule_display
            schedule_text = get_schedule_display(config)
            if schedule_text and schedule_text != 'No configurado':
                prompt_parts.append(
                    f'\nHORARIOS DE ATENCIÓN:\n{schedule_text}\n'
                    f'Usá esta información para responder consultas sobre horarios. '
                    f'Si el cliente pregunta por un día u horario en que el negocio está cerrado, informale claramente.'
                )
        except Exception:
            pass

        # Árbol completo del menú para contexto
        try:
            from apps.menu.services import MenuService
            menu_tree = MenuService.get_full_menu_tree_text(business)
            if menu_tree:
                prompt_parts.append(
                    f'\nMENÚ DEL NEGOCIO (opciones disponibles para el cliente):\n{menu_tree}'
                )
        except Exception:
            pass

        # Contexto de selecciones del menú
        if conversation and conversation.menu_selections:
            selections_text = '\n'.join(
                f'- {sel.get("category", "")} > {sel.get("subcategory", "")}'
                for sel in conversation.menu_selections
            )
            prompt_parts.append(
                f'\nEL USUARIO SELECCIONÓ LAS SIGUIENTES OPCIONES DEL MENÚ:\n{selections_text}\n'
                f'Tené en cuenta estas selecciones para dar una respuesta contextual.'
            )

        # Reglas generales
        prompt_parts.append(
            '\nREGLAS:'
            '\n- Respondé de forma profesional pero amigable'
            '\n- Sé conciso y directo'
            '\n- Si no sabés algo, decí que vas a consultar con el equipo'
            '\n- Usá español rioplatense (vos, tuteo argentino)'
            '\n- No inventes información que no tengas'
            '\n- Si el cliente necesita atención humana, ofrecé pasar con un recepcionista'
            '\n- NUNCA inventes ni generes un menú propio. El menú real del negocio se envía automáticamente por el sistema.'
            '\n- Si el usuario pide ver el menú o las opciones, respondé brevemente que se lo mostrás y el sistema se encargará.'
        )

        return '\n'.join(prompt_parts)

    def generate_response(self, conversation, user_message):
        """
        Genera una respuesta IA para un mensaje del usuario.
        Incluye historial de la conversación como contexto.

        Retorna: (text, usage_dict)
            - text: string con la respuesta
            - usage_dict: {'tokens': int, 'cost': float}
        """
        if not self.client:
            return (
                'Lo siento, el servicio de IA no está disponible en este momento. '
                'Un recepcionista te atenderá pronto.',
                {'tokens': 0, 'cost': 0.0}
            )

        try:
            business = conversation.business
            config = business.config
            model_name = config.ai_model or settings.OPENAI_DEFAULT_MODEL

            # Construir mensajes para ChatGPT
            system_prompt = self._build_system_prompt(business, conversation)
            messages = [{'role': 'system', 'content': system_prompt}]

            # Agregar historial (últimos 20 mensajes para no exceder contexto)
            recent_messages = list(
                conversation.messages.order_by('-created_at')[:20]
            )
            recent_messages.reverse()

            for msg in recent_messages:
                if msg.role == 'user':
                    messages.append({'role': 'user', 'content': msg.content})
                elif msg.role in ('assistant', 'agent'):
                    messages.append({'role': 'assistant', 'content': msg.content})

            # Agregar mensaje actual
            messages.append({'role': 'user', 'content': user_message})

            # Generar respuesta con retry automático
            response = self._call_with_retry(
                self.client.chat.completions.create,
                model=model_name,
                messages=messages,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            )

            usage = _usage_from_response(response, model_name)

            if response and response.choices:
                return response.choices[0].message.content, usage
            return (
                'Disculpá, no pude procesar tu mensaje. Un recepcionista te atenderá.',
                usage
            )

        except Exception as e:
            error_msg = str(e)
            if '429' in error_msg or 'rate_limit' in error_msg.lower():
                logger.warning(f'Cuota de OpenAI agotada: {e}')
                return (
                    '⏳ El servicio de IA está temporalmente saturado. '
                    'Un recepcionista te va a atender en breve.',
                    {'tokens': 0, 'cost': 0.0}
                )
            logger.error(f'Error generando respuesta ChatGPT: {e}')
            return (
                'Disculpá, hubo un error al procesar tu mensaje. '
                'Un recepcionista te va a atender pronto.',
                {'tokens': 0, 'cost': 0.0}
            )

    def classify_conversation(self, conversation):
        """
        Clasifica el interés del lead basándose en la conversación.

        Retorna: (classification_key, confidence, summary, usage_dict)
            - usage_dict: {'tokens': int, 'cost': float}
        """
        if not self.client:
            return None, 0.0, '', {'tokens': 0, 'cost': 0.0}

        try:
            business = conversation.business
            config = business.config
            model_name = config.ai_model or settings.OPENAI_DEFAULT_MODEL

            # Obtener categorías disponibles
            categories = config.classification_categories or settings.DEFAULT_LEAD_CLASSIFICATIONS
            categories_text = '\n'.join(
                f'- {cat["key"]}: {cat["label"]}'
                for cat in categories
            )

            # Obtener mensajes
            chat_messages = conversation.messages.order_by('created_at')
            chat_text = '\n'.join(
                f'[{msg.role}]: {msg.content}'
                for msg in chat_messages[:30]
            )

            # Selecciones del menú
            menu_context = ''
            if conversation.menu_selections:
                menu_context = 'Selecciones del menú: ' + ', '.join(
                    f'{sel.get("category", "")} > {sel.get("subcategory", "")}'
                    for sel in conversation.menu_selections
                )

            prompt = f"""Sos un analista experto en clasificación de leads. Analizá esta conversación de forma objetiva y metódica.

PASO 1 — EXTRAER SEÑALES:
Antes de clasificar, identificá estas señales en la conversación:
- Intención explícita: ¿El contacto dijo directamente qué quiere? (ej: "quiero un turno", "necesito presupuesto")
- Urgencia: ¿Hay presión de tiempo, mensajes repetidos, o tono urgente?
- Engagement: ¿Hace preguntas detalladas, da información específica, o responde con monosílabos?
- Sentimiento: ¿Está satisfecho, frustrado, indiferente, entusiasmado?
- Etapa del funnel: ¿Está explorando (awareness), comparando (consideration), o listo para actuar (decision)?
- Selecciones del menú: ¿Qué opciones navegó? Esto indica su interés inicial.

PASO 2 — CLASIFICAR CON EVIDENCIA:
Evaluá cuál categoría encaja mejor según las señales. No te guíes solo por una palabra suelta; considerá el contexto completo.

CATEGORÍAS DISPONIBLES:
{categories_text}

CALIBRACIÓN DE CONFIANZA:
- Menor a 0.4: Datos insuficientes (pocas interacciones, intención vaga)
- 0.4 a 0.7: Señal moderada (algo de intención pero ambigua)
- 0.7 a 0.9: Señal fuerte (intención clara y consistente)
- 0.9 o más: Muy explícito (el contacto dijo exactamente qué quiere)

{menu_context}

CONVERSACIÓN:
{chat_text}

PASO 3 — RESPONDER:
Respondé SOLO con un JSON válido, sin texto adicional:
{{
    "classification": "clave_de_la_categoria",
    "confidence": 0.0 a 1.0,
    "summary": "Qué quiere el contacto, nivel de urgencia, y detalles específicos mencionados (máximo 2 líneas)"
}}"""

            response = self._call_with_retry(
                self.client.chat.completions.create,
                model=model_name,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.3,
                max_tokens=350,
            )

            if not response or not response.choices:
                return None, 0.0, '', {'tokens': 0, 'cost': 0.0}

            usage = _usage_from_response(response, model_name)

            result_text = response.choices[0].message.content.strip()

            # Limpiar posible markdown
            if result_text.startswith('```'):
                result_text = result_text.split('\n', 1)[1]
                result_text = result_text.rsplit('```', 1)[0]

            result = json.loads(result_text)

            return (
                result.get('classification', ''),
                float(result.get('confidence', 0.0)),
                result.get('summary', ''),
                usage,
            )

        except Exception as e:
            logger.error(f'Error clasificando conversación: {e}')
            return None, 0.0, '', {'tokens': 0, 'cost': 0.0}

    def detect_human_request(self, message_text):
        """
        Usa la IA para detectar si el usuario está pidiendo hablar con un humano/agente.
        Retorna True si detecta la intención, False si no.
        Esto permite manejar errores de ortografía, jerga y formas inesperadas.
        """
        if not self.client:
            return False

        try:
            prompt = (
                'Analizá el siguiente mensaje de un cliente y determiná si está pidiendo '
                'hablar con una persona real, un agente humano, un recepcionista, o ser derivado/transferido '
                'a atención humana. Considerá errores de ortografía, jerga y formas coloquiales.\n\n'
                f'Mensaje: "{message_text}"\n\n'
                'Respondé SOLO con "SI" o "NO".'
            )

            response = self._call_with_retry(
                self.client.chat.completions.create,
                model='gpt-4o-mini',
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.1,
                max_tokens=5,
            )

            if response and response.choices:
                answer = response.choices[0].message.content.strip().upper()
                return answer.startswith('SI') or answer == 'SÍ'
            return False
        except Exception as e:
            logger.error(f'Error detectando pedido de humano: {e}')
            return False

    def transcribe_audio(self, audio_url):
        """
        Descarga un archivo de audio de la URL (Cloudinary) y lo transcribe usando OpenAI Whisper.
        Retorna: (text, usage_dict)
        """
        if not self.client:
            return '', {'tokens': 0, 'cost': 0.0}

        try:
            import requests
            import io
            
            # Descargar audio
            resp = requests.get(audio_url, timeout=30)
            resp.raise_for_status()
            
            # Detectar formato (Meta suele mandar ogg/opus, Whisper soporta ogg)
            audio_file = io.BytesIO(resp.content)
            audio_file.name = 'voice_note.ogg'  # Extensión necesaria para OpenAI
            
            # Transcribir
            transcript = self._call_with_retry(
                self.client.audio.transcriptions.create,
                model='whisper-1',
                file=audio_file
            )
            
            text = transcript.text if transcript else ''
            
            # Costo fijo aproximado por transcripción (Whisper cobra $0.006 / minuto)
            # Para notas de voz cortas ponemos un costo base simbólico
            usage = {'tokens': 0, 'cost': 0.001}
            
            return text, usage

        except Exception as e:
            logger.error(f'Error transcribiendo audio con Whisper: {e}')
            return '', {'tokens': 0, 'cost': 0.0}

    def summarize_for_agent(self, conversation):
        """
        Genera un resumen corto y claro para el recepcionista.
        Incluye qué quiere el cliente y sus selecciones del menú.
        """
        if not self.client:
            return ''

        try:
            business = conversation.business
            config = business.config
            model_name = config.ai_model or settings.OPENAI_DEFAULT_MODEL

            chat_messages = conversation.messages.order_by('created_at')
            chat_text = '\n'.join(
                f'[{msg.role}]: {msg.content}'
                for msg in chat_messages[:30]
            )

            menu_context = ''
            if conversation.menu_selections:
                menu_context = '\nSelecciones del menú:\n' + '\n'.join(
                    f'- {sel.get("category", "")} > {sel.get("subcategory", "")}'
                    for sel in conversation.menu_selections
                )

            prompt = f"""Generá un resumen MUY breve (máximo 2 líneas) de lo que quiere este cliente.
El resumen es para un recepcionista que necesita entender rápidamente qué necesita el cliente.

{menu_context}

CONVERSACIÓN:
{chat_text}

Respondé SOLO con el resumen, sin formato extra."""

            response = self._call_with_retry(
                self.client.chat.completions.create,
                model=model_name,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.3,
                max_tokens=150,
            )

            if response and response.choices:
                return response.choices[0].message.content.strip()
            return ''

        except Exception as e:
            logger.error(f'Error generando resumen: {e}')
            return ''


# Instancia global del servicio
ai_service = ChatGPTService()
