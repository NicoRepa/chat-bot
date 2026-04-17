"""
Vistas del panel de gestión para recepcionistas.
Dashboard, lista de conversaciones, detalle con chat y configuración.
"""
import csv
import json
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db.models import Count, Q
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from apps.core.models import Business, BusinessConfig, UserProfile
from apps.conversations.models import Conversation, Message, Contact, ContactNote, Tag, AIFeedback
from apps.menu.models import MenuCategory, MenuSubcategory, MenuSubSubcategory
from apps.menu.services import MenuService
from apps.webhooks.services import ChatOrchestrator
from apps.webhooks.whatsapp_service import WhatsAppService
from apps.ai_engine.services import ai_service


def _user_can_manage(user):
    """Helper: True si el usuario es superuser, admin o supervisor."""
    if user.is_superuser:
        return True
    try:
        return user.profile.is_admin or user.profile.is_supervisor
    except Exception:
        return False


def _user_is_admin(user):
    """Helper: True si el usuario es superuser o admin."""
    if user.is_superuser:
        return True
    try:
        return user.profile.is_admin
    except Exception:
        return False


def _user_is_agent_only(user):
    """Helper: True si el usuario es solo agente (no admin/supervisor/superuser)."""
    if user.is_superuser:
        return False
    try:
        return user.profile.is_agent
    except Exception:
        return False

class DashboardView(LoginRequiredMixin, View):
    """Dashboard principal con métricas del negocio."""
    login_url = '/admin/login/'

    def get(self, request):
        business = Business.objects.filter(is_active=True).select_related('config').first()

        if not business:
            return render(request, 'panel/dashboard.html', {
                'no_business': True,
            })

        config = business.config
        conversations = Conversation.objects.filter(business=business)
        
        # Filtrar por rol: respetar visibilidad configurable
        if _user_is_agent_only(request.user) and not config.supervisor_only_mode:
            if config.agent_visibility_mode == 'assigned_only':
                conversations = conversations.filter(assigned_to=request.user)
            else:
                conversations = conversations.filter(
                    Q(assigned_to=request.user) | Q(assigned_to__isnull=True)
                )

        # Métricas
        total_conversations = conversations.count()
        active_conversations = conversations.filter(status='activa').count()
        waiting_human = conversations.filter(
            is_ai_active=False, status='activa'
        ).count()
        finalized = conversations.filter(status='finalizada').count()

        # Clasificaciones
        classifications = conversations.exclude(
            classification=''
        ).values('classification').annotate(
            count=Count('classification')
        ).order_by('-count')

        try:
            config = business.config
            cat_map = {
                cat['key']: cat for cat in config.classification_categories
            }
        except Exception:
            cat_map = {}

        classification_stats = []
        for c in classifications:
            info = cat_map.get(c['classification'], {
                'label': c['classification'], 'icon': '🏷️', 'color': '#6B7280'
            })
            classification_stats.append({
                'key': c['classification'],
                'label': info.get('label', c['classification']),
                'icon': info.get('icon', '🏷️'),
                'color': info.get('color', '#6B7280'),
                'count': c['count'],
            })

        status_stats = conversations.values('status').annotate(
            count=Count('status')
        ).order_by('-count')

        recent = conversations.select_related('contact', 'assigned_to').order_by('-updated_at')[:10]

        context = {
            'business': business,
            'total_conversations': total_conversations,
            'active_conversations': active_conversations,
            'waiting_human': waiting_human,
            'finalized': finalized,
            'classification_stats': classification_stats,
            'status_stats': status_stats,
            'recent_conversations': recent,
        }
        return render(request, 'panel/dashboard.html', context)


class ConversationListView(LoginRequiredMixin, View):
    """Lista de conversaciones con filtros."""
    login_url = '/admin/login/'

    def get(self, request):
        business = Business.objects.filter(is_active=True).select_related('config').first()
        if not business:
            return redirect('panel:dashboard')

        config = business.config
        conversations = Conversation.objects.filter(
            business=business
        ).select_related('contact', 'assigned_to').order_by('-updated_at')
        
        # Filtro de seguridad: respetar visibilidad configurable
        if _user_is_agent_only(request.user) and not config.supervisor_only_mode:
            if config.agent_visibility_mode == 'assigned_only':
                conversations = conversations.filter(assigned_to=request.user)
            else:
                conversations = conversations.filter(
                    Q(assigned_to=request.user) | Q(assigned_to__isnull=True)
                )

        # Filtros GET
        classification_filter = request.GET.get('classification', '')
        status_filter = request.GET.get('status')
        search_query = request.GET.get('search')
        ai_filter = request.GET.get('ai', '')
        assigned_filter = request.GET.get('assigned', '')

        tag_filter = request.GET.get('tag', '')

        if classification_filter:
            conversations = conversations.filter(classification=classification_filter)
        if status_filter:
            conversations = conversations.filter(status=status_filter)
        if tag_filter:
            conversations = conversations.filter(tags__id=tag_filter)
        if search_query:
            conversations = conversations.filter(
                Q(contact__name__icontains=search_query) |
                Q(contact__external_id__icontains=search_query) |
                Q(summary__icontains=search_query)
            )
        if ai_filter == 'off':
            conversations = conversations.filter(is_ai_active=False)
        elif ai_filter == 'on':
            conversations = conversations.filter(is_ai_active=True)
            
        if assigned_filter == 'unassigned':
            conversations = conversations.filter(assigned_to__isnull=True)
        elif assigned_filter == 'me':
            conversations = conversations.filter(assigned_to=request.user)
        elif assigned_filter.isdigit():
            conversations = conversations.filter(assigned_to_id=assigned_filter)

        try:
            config = business.config
            classifications = config.classification_categories
        except Exception:
            classifications = []

        # Obtener lista de agentes del negocio para el filtro
        agents = User.objects.filter(profile__business=business)
        tags = business.tags.all()

        context = {
            'business': business,
            'conversations': conversations,
            'classifications': classifications,
            'current_classification': classification_filter,
            'current_status': status_filter,
            'current_search': search_query,
            'current_ai': ai_filter,
            'current_assigned': assigned_filter,
            'current_tag': tag_filter,
            'agents': agents,
            'tags': tags,
            'status_choices': Conversation.STATUS_CHOICES,
        }
        return render(request, 'panel/conversation_list.html', context)


class ConversationDetailView(LoginRequiredMixin, View):
    """Detalle de conversación con chat, resumen IA y acciones."""
    login_url = '/admin/login/'

    def get(self, request, conversation_id):
        conversation = get_object_or_404(
            Conversation.objects.select_related('contact', 'business__config', 'assigned_to'),
            pk=conversation_id
        )

        # Verificar permisos de visibilidad
        try:
            config = conversation.business.config
        except Exception:
            config = None
        if config and _user_is_agent_only(request.user) and not config.supervisor_only_mode:
            if config.agent_visibility_mode == 'assigned_only':
                if conversation.assigned_to != request.user:
                    return redirect('panel:conversation_list')
            else:
                if conversation.assigned_to and conversation.assigned_to != request.user:
                    return redirect('panel:conversation_list')

        messages = conversation.messages.order_by('created_at')
        classification_info = conversation.get_classification_display_info()

        # Resetear contador de no leídos al abrir la conversación
        if conversation.panel_unread_count > 0:
            conversation.panel_unread_count = 0
            conversation.save(update_fields=['panel_unread_count'])

        try:
            classifications = conversation.business.config.classification_categories
        except Exception:
            classifications = []

        # Agentes para dropdown de asignación
        agents = User.objects.filter(profile__business=conversation.business)
        tags = conversation.business.tags.all()

        context = {
            'conversation': conversation,
            'messages': messages,
            'classification_info': classification_info,
            'business': conversation.business,
            'classifications': classifications,
            'agents': agents,
            'tags': tags,
            'status_choices': Conversation.STATUS_CHOICES,
        }
        return render(request, 'panel/conversation_detail.html', context)


@method_decorator(csrf_exempt, name='dispatch')
class PanelReplyView(LoginRequiredMixin, View):
    """El recepcionista responde desde el panel."""
    login_url = '/admin/login/'

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        message_text = request.POST.get('message', '').strip()
        media_file = request.FILES.get('media_file')
        
        if not message_text and not media_file:
            return redirect('panel:conversation_detail', conversation_id=conversation_id)

        # Verificar permisos: agentes solo pueden responder sus propios chats
        if _user_is_agent_only(request.user):
            if conversation.assigned_to and conversation.assigned_to != request.user:
                return redirect('panel:conversation_detail', conversation_id=conversation_id)

        ChatOrchestrator.send_agent_reply(conversation, message_text, media_file=media_file)
        
        # Reset SLA timer since a human has responded
        if conversation.human_needed_at:
            conversation.human_needed_at = None
            conversation.save(update_fields=['human_needed_at'])
            
        return redirect('panel:conversation_detail', conversation_id=conversation_id)


@method_decorator(csrf_exempt, name='dispatch')
class PanelToggleAIView(LoginRequiredMixin, View):
    """Activa/desactiva la IA en una conversación."""
    login_url = '/admin/login/'

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        if conversation.is_ai_active:
            # Tomar control: Desactivar IA y asignar si no tiene a quien lo tomó
            ChatOrchestrator.takeover_conversation(conversation)
            
            # Auto-assign if unassigned
            if not conversation.assigned_to:
                conversation.assigned_to = request.user
            
            # Set human needed at if not set
            if not conversation.human_needed_at:
                conversation.human_needed_at = timezone.now()
            
            conversation.save(update_fields=['assigned_to', 'human_needed_at'])
        else:
            ChatOrchestrator.activate_ai(conversation)
            conversation.human_needed_at = None
            conversation.save(update_fields=['human_needed_at'])
            
        return redirect('panel:conversation_detail', conversation_id=conversation_id)


@method_decorator(csrf_exempt, name='dispatch')
class PanelAssignAgentView(LoginRequiredMixin, View):
    """Asigna una conversación a un agente específico."""
    login_url = '/admin/login/'

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        agent_id = request.POST.get('agent_id')
        
        if agent_id:
            agent = get_object_or_404(User, pk=agent_id)
            conversation.assigned_to = agent
        else:
            # Unassign
            conversation.assigned_to = None
            
        conversation.save(update_fields=['assigned_to'])
        return redirect('panel:conversation_detail', conversation_id=conversation_id)


@method_decorator(csrf_exempt, name='dispatch')
class PanelUpdateStatusView(LoginRequiredMixin, View):
    """Cambiar el estado de una conversación."""
    login_url = '/admin/login/'

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        new_status = request.POST.get('status', '')
        valid_statuses = [s[0] for s in Conversation.STATUS_CHOICES]
        if new_status in valid_statuses:
            conversation.status = new_status
            
            # Reset SLA if finalized
            if new_status == 'finalizada' and conversation.human_needed_at:
                conversation.human_needed_at = None
                conversation.save(update_fields=['status', 'human_needed_at'])
            else:
                conversation.save(update_fields=['status'])
                
        return redirect('panel:conversation_detail', conversation_id=conversation_id)


@method_decorator(csrf_exempt, name='dispatch')
class PanelUpdateClassificationView(LoginRequiredMixin, View):
    """El recepcionista cambia la clasificación manualmente."""
    login_url = '/admin/login/'

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        new_classification = request.POST.get('classification', '')
        if new_classification:
            conversation.classification = new_classification
            conversation.save(update_fields=['classification'])
        return redirect('panel:conversation_detail', conversation_id=conversation_id)


@method_decorator(csrf_exempt, name='dispatch')
class PanelToggleTagView(LoginRequiredMixin, View):
    """El recepcionista agrega o quita una etiqueta a la conversación."""
    login_url = '/admin/login/'

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        tag_id = request.POST.get('tag_id')
        if tag_id:
            tag = get_object_or_404(Tag, id=tag_id, business=conversation.business)
            if tag in conversation.tags.all():
                conversation.tags.remove(tag)
            else:
                conversation.tags.add(tag)
        return redirect('panel:conversation_detail', conversation_id=conversation_id)


@method_decorator(csrf_exempt, name='dispatch')
class PanelResendMenuView(LoginRequiredMixin, View):
    """Reenviar el menú interactivo en la conversación."""
    login_url = '/admin/login/'

    def post(self, request, conversation_id):
        conversation = get_object_or_404(
            Conversation.objects.select_related('business'),
            pk=conversation_id
        )
        business = conversation.business

        # Generar y enviar menú
        greeting, has_menu = MenuService.get_greeting_with_menu(business)
        if greeting:
            Message.objects.create(
                conversation=conversation,
                role='assistant',
                content=greeting
            )
            # Enviar a WhatsApp
            if conversation.contact.platform == 'whatsapp':
                try:
                    config = business.config
                    if config.whatsapp_token and config.whatsapp_phone_id:
                        WhatsAppService.send_text_message(
                            phone_number_id=config.whatsapp_phone_id,
                            access_token=config.whatsapp_token,
                            recipient=conversation.contact.external_id,
                            text=greeting
                        )
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f'Error enviando menú por WhatsApp: {e}')

            if has_menu:
                conversation.menu_state = 'main_menu'
            else:
                conversation.menu_state = 'ai_chat'
            conversation.is_ai_active = True
            conversation.save(update_fields=['menu_state', 'is_ai_active'])

        return redirect('panel:conversation_detail', conversation_id=conversation_id)


@method_decorator(csrf_exempt, name='dispatch')
class PanelRefreshSummaryView(LoginRequiredMixin, View):
    """Regenerar resumen IA de una conversación."""
    login_url = '/admin/login/'

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        classification, confidence, summary = ai_service.classify_conversation(conversation)
        if summary:
            conversation.summary = summary
        if classification:
            conversation.classification = classification
            conversation.classification_confidence = confidence
        conversation.save(update_fields=['summary', 'classification', 'classification_confidence'])
        return redirect('panel:conversation_detail', conversation_id=conversation_id)


class PanelExportCSVView(LoginRequiredMixin, View):
    """Exportar lista de conversaciones filtradas a CSV."""
    login_url = '/admin/login/'

    def get(self, request):
        business = Business.objects.filter(is_active=True).first()
        if not business:
            return redirect('panel:dashboard')

        config = business.config
        conversations = Conversation.objects.filter(
            business=business
        ).select_related('contact', 'assigned_to').order_by('-updated_at')
        
        # Filtro de seguridad
        if _user_is_agent_only(request.user) and not config.supervisor_only_mode:
            if config.agent_visibility_mode == 'assigned_only':
                conversations = conversations.filter(assigned_to=request.user)
            else:
                conversations = conversations.filter(
                    Q(assigned_to=request.user) | Q(assigned_to__isnull=True)
                )

        # Filtros
        classification_filter = request.GET.get('classification', '')
        status_filter = request.GET.get('status')
        search_query = request.GET.get('search')
        ai_filter = request.GET.get('ai', '')
        assigned_filter = request.GET.get('assigned', '')
        tag_filter = request.GET.get('tag', '')

        if classification_filter:
            conversations = conversations.filter(classification=classification_filter)
        if status_filter:
            conversations = conversations.filter(status=status_filter)
        if tag_filter:
            conversations = conversations.filter(tags__id=tag_filter)
        if search_query:
            conversations = conversations.filter(
                Q(contact__name__icontains=search_query) |
                Q(contact__external_id__icontains=search_query) |
                Q(summary__icontains=search_query)
            )
        if ai_filter == 'off':
            conversations = conversations.filter(is_ai_active=False)
        elif ai_filter == 'on':
            conversations = conversations.filter(is_ai_active=True)
            
        if assigned_filter == 'unassigned':
            conversations = conversations.filter(assigned_to__isnull=True)
        elif assigned_filter == 'me':
            conversations = conversations.filter(assigned_to=request.user)
        elif assigned_filter.isdigit():
            conversations = conversations.filter(assigned_to_id=assigned_filter)

        response = HttpResponse(
            content_type='text/csv; charset=utf-8-sig',
            headers={'Content-Disposition': 'attachment; filename="conversaciones.csv"'},
        )
        
        writer = csv.writer(response)
        writer.writerow([
            'ID', 'Nombre Contacto', 'Teléfono/ID', 'Plataforma', 'Estado', 
            'Asignado a', 'Clasificación', 'Confianza IA', 'Etiquetas', 'Resumen IA', 
            'IA Activa', 'Fecha Creación', 'Última Actividad', 'Link'
        ])

        # Optimize for tags fetching
        conversations = conversations.prefetch_related('tags')

        for conv in conversations:
            assigned = conv.assigned_to.username if conv.assigned_to else 'Sin asignar'
            tags_str = ', '.join(t.name for t in conv.tags.all())
            link = request.build_absolute_uri(f'/panel/conversaciones/{conv.id}/')
            
            writer.writerow([
                str(conv.id),
                conv.contact.name or '',
                conv.contact.external_id,
                conv.contact.get_platform_display(),
                conv.get_status_display(),
                assigned,
                conv.get_classification_display(),
                f"{conv.classification_confidence:.2f}" if conv.classification_confidence else '',
                tags_str,
                conv.summary,
                'Sí' if conv.is_ai_active else 'No',
                conv.created_at.astimezone().strftime('%Y-%m-%d %H:%M:%S'),
                conv.updated_at.astimezone().strftime('%Y-%m-%d %H:%M:%S'),
                link
            ])

        return response


class MenuConfigView(LoginRequiredMixin, View):
    """Configuración del menú interactivo desde el panel."""
    login_url = '/admin/login/'

    def get(self, request):
        business = Business.objects.filter(is_active=True).first()
        if not business:
            return redirect('panel:dashboard')

        categories = MenuCategory.objects.filter(
            business=business
        ).prefetch_related('subcategories', 'subcategories__children').order_by('order')

        context = {
            'business': business,
            'categories': categories,
        }
        return render(request, 'panel/menu_config.html', context)


class SimulatorView(LoginRequiredMixin, View):
    """Simulador de chat."""
    login_url = '/admin/login/'

    def get(self, request):
        business = Business.objects.filter(is_active=True).select_related('config').first()
        if not business:
            return redirect('panel:dashboard')
        api_key = ''
        try:
            api_key = business.config.webhook_secret
        except Exception:
            pass
        context = {
            'business': business,
            'api_key': api_key,
        }
        return render(request, 'panel/simulator.html', context)

class SettingsView(LoginRequiredMixin, View):
    """Configuración general del negocio."""
    login_url = '/admin/login/'

    def get(self, request):
        business = Business.objects.filter(is_active=True).select_related('config').first()
        if not business:
            return redirect('panel:dashboard')

        config = business.config
        
        # Inyectar schedule por defecto si está vacío para que el template lo pinte
        if not config.business_hours:
            from apps.core.schedule_utils import DEFAULT_SCHEDULE
            config.business_hours = DEFAULT_SCHEDULE

        context = {
            'business': business,
            'config': config,
            'saved': request.GET.get('saved', '') == '1',
        }
        return render(request, 'panel/settings.html', context)

    def post(self, request):
        business = Business.objects.filter(is_active=True).select_related('config').first()
        if not business:
            return redirect('panel:dashboard')

        # Actualizar Business
        business.name = request.POST.get('business_name', '')
        business.industry = request.POST.get('industry', '')
        business.address = request.POST.get('address', '')
        business.phone = request.POST.get('phone', '')
        business.email = request.POST.get('email', '')
        business.save(update_fields=['name', 'industry', 'address', 'phone', 'email'])

        # Actualizar Config
        config = business.config
        config.ai_model = request.POST.get('ai_model', 'gpt-4o-mini')
        config.system_prompt = request.POST.get('system_prompt', '')
        try:
            config.temperature = float(request.POST.get('temperature', 0.7))
        except ValueError:
            pass
        config.knowledge_base = request.POST.get('knowledge_base', '')
        config.greeting_message = request.POST.get('greeting_message', '')
        config.menu_enabled = request.POST.get('menu_enabled') == 'on'
        config.webhook_secret = request.POST.get('webhook_secret', '')
        config.whatsapp_phone_id = request.POST.get('whatsapp_phone_id', '')
        config.whatsapp_token = request.POST.get('whatsapp_token', '')
        config.whatsapp_verify_token = request.POST.get('whatsapp_verify_token', '')
        config.auto_assign_enabled = request.POST.get('auto_assign_enabled') == 'on'
        try:
            config.ai_max_messages = int(request.POST.get('ai_max_messages', 0))
        except (ValueError, TypeError):
            config.ai_max_messages = 0
        try:
            config.auto_close_hours = int(request.POST.get('auto_close_hours', 0))
        except (ValueError, TypeError):
            config.auto_close_hours = 0
        config.agent_visibility_mode = request.POST.get('agent_visibility_mode', 'all')
        config.supervisor_only_mode = request.POST.get('supervisor_only_mode') == 'on'
        config.menu_force_selection = request.POST.get('menu_force_selection') == 'on'
        config.menu_reactivation_message = request.POST.get('menu_reactivation_message', '')
        config.welcome_back_message = request.POST.get('welcome_back_message', '')
        config.escalation_message = request.POST.get('escalation_message', '')
        config.out_of_hours_message = request.POST.get('out_of_hours_message', '')
        config.business_hours_enabled = request.POST.get('business_hours_enabled') == 'on'

        # Horarios de atención — parsear los 7 días
        import json
        days = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
        schedule = []
        for day in days:
            schedule.append({
                'day': day,
                'enabled': request.POST.get(f'schedule_{day}_enabled') == 'on',
                'start': request.POST.get(f'schedule_{day}_start', ''),
                'end': request.POST.get(f'schedule_{day}_end', ''),
            })
        config.business_hours = schedule

        config.save(update_fields=[
            'ai_model', 'system_prompt', 'temperature',
            'knowledge_base', 'greeting_message',
            'menu_enabled', 'webhook_secret',
            'whatsapp_phone_id', 'whatsapp_token', 'whatsapp_verify_token',
            'auto_assign_enabled', 'ai_max_messages', 'auto_close_hours',
            'agent_visibility_mode', 'supervisor_only_mode',
            'menu_force_selection', 'menu_reactivation_message',
            'welcome_back_message', 'escalation_message', 'out_of_hours_message',
            'business_hours', 'business_hours_enabled',
        ])

        return redirect('/panel/settings/?saved=1')


class PanelMessagesView(LoginRequiredMixin, View):
    """Devuelve los mensajes generados después de un timestamp dado para Polling AJAX."""
    login_url = '/admin/login/'

    def get(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        last_msg_id = request.GET.get('last_msg_id')
        
        # Filtramos los mensajes que se crearon después del último que tiene el frontend
        messages_qs = conversation.messages.order_by('created_at')
        if last_msg_id:
            try:
                # Obtenemos el mensaje de referencia
                last_msg = conversation.messages.get(pk=last_msg_id)
                messages_qs = messages_qs.filter(created_at__gt=last_msg.created_at)
            except Exception:
                pass
        
        messages_data = []
        for msg in messages_qs:
            messages_data.append({
                'id': str(msg.id),
                'role': msg.role,
                'content': msg.content,
                'metadata': msg.metadata,
                'created_at': msg.created_at.strftime("%d/%m/%Y %H:%M"),
            })
            
        return JsonResponse({'messages': messages_data})


class PanelNotificationsView(LoginRequiredMixin, View):
    """Endpoint AJAX para devolver notificaciones de SLA (chats esperando humano)."""
    login_url = '/admin/login/'
    
    def get(self, request):
        business = Business.objects.filter(is_active=True).select_related('config').first()
        if not business:
            return JsonResponse({'notifications': []})

        config = business.config
        # Buscar conversaciones que necesitan humano
        qs = Conversation.objects.filter(
            business=business, 
            status='activa', 
            is_ai_active=False
        )
        
        # Filtrar si es agente: respetar visibilidad configurable
        if _user_is_agent_only(request.user) and not config.supervisor_only_mode:
            if config.agent_visibility_mode == 'assigned_only':
                qs = qs.filter(assigned_to=request.user)
            else:
                qs = qs.filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True))
            
        now = timezone.now()
        notifications = []
        for conv in qs:
            time_waiting = 0
            is_sla_breach = False
            
            if conv.human_needed_at:
                delta = now - conv.human_needed_at
                time_waiting = int(delta.total_seconds() / 60) # en minutos
                if time_waiting > 10:
                    is_sla_breach = True
                    
            notifications.append({
                'id': str(conv.id),
                'contact_name': conv.contact.name or conv.contact.external_id,
                'time_waiting_mins': time_waiting,
                'is_sla_breach': is_sla_breach
            })
            
        return JsonResponse({'notifications': notifications})


# ── Menu CRUD Views ──────────────────────────────────────

@method_decorator(csrf_exempt, name='dispatch')
class MenuCategoryCreateView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def post(self, request):
        business = Business.objects.filter(is_active=True).first()
        MenuCategory.objects.create(
            business=business,
            name=request.POST.get('name', '').strip(),
            emoji=request.POST.get('emoji', '').strip(),
            description=request.POST.get('description', '').strip(),
            order=int(request.POST.get('order', 0) or 0),
            is_active=request.POST.get('is_active') == 'on',
        )
        return redirect('panel:menu_config')


@method_decorator(csrf_exempt, name='dispatch')
class MenuCategoryUpdateView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def post(self, request, category_id):
        cat = get_object_or_404(MenuCategory, pk=category_id)
        cat.name = request.POST.get('name', cat.name).strip()
        cat.emoji = request.POST.get('emoji', cat.emoji).strip()
        cat.description = request.POST.get('description', cat.description).strip()
        cat.order = int(request.POST.get('order', cat.order) or 0)
        cat.is_active = request.POST.get('is_active') == 'on'
        cat.save()
        return redirect('panel:menu_config')


@method_decorator(csrf_exempt, name='dispatch')
class MenuCategoryDeleteView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def post(self, request, category_id):
        cat = get_object_or_404(MenuCategory, pk=category_id)
        cat.delete()
        return redirect('panel:menu_config')


@method_decorator(csrf_exempt, name='dispatch')
class MenuSubcategoryCreateView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def post(self, request):
        category = get_object_or_404(MenuCategory, pk=request.POST.get('category_id'))
        MenuSubcategory.objects.create(
            category=category,
            name=request.POST.get('name', '').strip(),
            emoji=request.POST.get('emoji', '').strip(),
            description=request.POST.get('description', '').strip(),
            auto_response=request.POST.get('auto_response', '').strip(),
            order=int(request.POST.get('order', 0) or 0),
            is_active=request.POST.get('is_active') == 'on',
        )
        return redirect('panel:menu_config')


@method_decorator(csrf_exempt, name='dispatch')
class MenuSubcategoryUpdateView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def post(self, request, subcategory_id):
        sub = get_object_or_404(MenuSubcategory, pk=subcategory_id)
        sub.name = request.POST.get('name', sub.name).strip()
        sub.emoji = request.POST.get('emoji', sub.emoji).strip()
        sub.description = request.POST.get('description', sub.description).strip()
        sub.auto_response = request.POST.get('auto_response', sub.auto_response).strip()
        sub.order = int(request.POST.get('order', sub.order) or 0)
        sub.is_active = request.POST.get('is_active') == 'on'
        sub.save()
        return redirect('panel:menu_config')


@method_decorator(csrf_exempt, name='dispatch')
class MenuSubcategoryDeleteView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def post(self, request, subcategory_id):
        sub = get_object_or_404(MenuSubcategory, pk=subcategory_id)
        sub.delete()
        return redirect('panel:menu_config')


@method_decorator(csrf_exempt, name='dispatch')
class MenuSubSubcategoryCreateView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def post(self, request):
        subcategory = get_object_or_404(MenuSubcategory, pk=request.POST.get('subcategory_id'))
        MenuSubSubcategory.objects.create(
            subcategory=subcategory,
            name=request.POST.get('name', '').strip(),
            emoji=request.POST.get('emoji', '').strip(),
            description=request.POST.get('description', '').strip(),
            auto_response=request.POST.get('auto_response', '').strip(),
            order=int(request.POST.get('order', 0) or 0),
            is_active=request.POST.get('is_active') == 'on',
        )
        return redirect('panel:menu_config')


@method_decorator(csrf_exempt, name='dispatch')
class MenuSubSubcategoryUpdateView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def post(self, request, item_id):
        item = get_object_or_404(MenuSubSubcategory, pk=item_id)
        item.name = request.POST.get('name', item.name).strip()
        item.emoji = request.POST.get('emoji', item.emoji).strip()
        item.description = request.POST.get('description', item.description).strip()
        item.auto_response = request.POST.get('auto_response', item.auto_response).strip()
        item.order = int(request.POST.get('order', item.order) or 0)
        item.is_active = request.POST.get('is_active') == 'on'
        item.save()
        return redirect('panel:menu_config')


@method_decorator(csrf_exempt, name='dispatch')
class MenuSubSubcategoryDeleteView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def post(self, request, item_id):
        item = get_object_or_404(MenuSubSubcategory, pk=item_id)
        item.delete()
        return redirect('panel:menu_config')


class ContactListView(LoginRequiredMixin, View):
    """Mini-CRM: Lista de contactos (directorio)."""
    login_url = '/admin/login/'
    
    def get(self, request):
        business = Business.objects.filter(is_active=True).first()
        if not business:
            return redirect('panel:dashboard')
            
        contacts = Contact.objects.filter(business=business).order_by('-created_at')
        
        search = request.GET.get('search', '')
        if search:
            contacts = contacts.filter(
                Q(name__icontains=search) | 
                Q(external_id__icontains=search) |
                Q(phone__icontains=search) |
                Q(email__icontains=search)
            )

        context = {
            'business': business,
            'contacts': contacts,
            'current_search': search,
        }
        return render(request, 'panel/contact_list.html', context)


class ContactDetailView(LoginRequiredMixin, View):
    """Mini-CRM: Detalle del contacto, historial y notas."""
    login_url = '/admin/login/'
    
    def get(self, request, contact_id):
        contact = get_object_or_404(Contact, pk=contact_id)
        business = contact.business
        
        # Historial de conversaciones
        conversations = Conversation.objects.filter(contact=contact).order_by('-created_at')
        
        # Notas
        notes = contact.notes.all().select_related('author')

        context = {
            'business': business,
            'contact': contact,
            'notes': notes,
            'conversations': conversations,
        }
        return render(request, 'panel/contact_detail.html', context)
        
    def post(self, request, contact_id):
        contact = get_object_or_404(Contact, pk=contact_id)
        note_content = request.POST.get('note', '').strip()
        
        if note_content:
            ContactNote.objects.create(
                contact=contact,
                author=request.user,
                content=note_content
            )
            
        return redirect('panel:contact_detail', contact_id=contact_id)

class AgentListView(LoginRequiredMixin, View):
    """Lista de agentes/empleados del negocio."""
    login_url = '/admin/login/'
    
    def get(self, request):
        business = Business.objects.filter(is_active=True).select_related('config').first()
        if not business:
            return redirect('panel:dashboard')
            
        # Solo Admin o Supervisor pueden ver y gestionar agentes
        if not _user_can_manage(request.user):
            return redirect('panel:dashboard')

        agents = UserProfile.objects.filter(business=business).select_related('user')

        # Calcular chats activos por agente
        for agent in agents:
            agent.active_count = Conversation.objects.filter(
                assigned_to=agent.user,
                status__in=['activa', 'en_seguimiento', 'esperando_cliente']
            ).count()

        # Obtener clasificaciones para mostrar labels
        try:
            classifications = business.config.classification_categories or []
        except Exception:
            classifications = []
        cat_map = {cat['key']: cat for cat in classifications}

        context = {
            'business': business,
            'agents': agents,
            'cat_map': cat_map,
            'is_admin': _user_is_admin(request.user),
        }
        return render(request, 'panel/agent_list.html', context)


class PanelConversationUpdatesView(LoginRequiredMixin, View):
    """Snapshot liviano de conversaciones para polling de la lista (badges no leídos)."""
    login_url = '/admin/login/'

    def get(self, request):
        business = Business.objects.filter(is_active=True).first()
        if not business:
            return JsonResponse({'conversations': []})

        qs = Conversation.objects.filter(
            business=business
        ).select_related('contact').order_by('-updated_at')

        try:
            config = business.config
        except Exception:
            config = None
        if config and _user_is_agent_only(request.user) and not config.supervisor_only_mode:
            if config.agent_visibility_mode == 'assigned_only':
                qs = qs.filter(assigned_to=request.user)
            else:
                qs = qs.filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True))

        qs = qs[:50]

        data = []
        for conv in qs:
            # Último mensaje del cliente (para preview del toast)
            last_user_msg = conv.messages.filter(role='user').order_by('-created_at').first()
            # Último mensaje de cualquier tipo (para detectar actividad nueva)
            last_any_msg = conv.messages.order_by('-created_at').first()
            preview = ''
            if last_user_msg:
                preview = last_user_msg.content[:60] + '...' if len(last_user_msg.content) > 60 else last_user_msg.content
            classification_info = conv.get_classification_display_info()
            contact = conv.contact
            assigned_display = ''
            if conv.assigned_to:
                assigned_display = conv.assigned_to.first_name or conv.assigned_to.username
            data.append({
                'id': str(conv.id),
                'contact_id': str(contact.id),
                'contact_name': contact.name or contact.external_id,
                'contact_initial': (contact.name or contact.external_id or '?')[0].upper(),
                'contact_platform': contact.platform,
                'contact_external_id': contact.external_id[:18] if contact.external_id else '',
                'assigned_to_display': assigned_display,
                'summary': conv.summary or '',
                'last_msg_id': str(last_any_msg.id) if last_any_msg else None,
                'last_msg_preview': preview,
                'updated_at': conv.updated_at.isoformat(),
                'status': conv.status,
                'status_display': conv.get_status_display(),
                'is_ai_active': conv.is_ai_active,
                'panel_unread_count': conv.panel_unread_count or 0,
                'classification_info': {
                    'key': classification_info.get('key', ''),
                    'label': classification_info.get('label', 'Sin clasificar'),
                    'icon': classification_info.get('icon', '🔘'),
                    'color': classification_info.get('color', '#9CA3AF'),
                },
            })

        # Stats para el dashboard
        all_convs = Conversation.objects.filter(business=business)
        
        if config and _user_is_agent_only(request.user) and not config.supervisor_only_mode:
            if config.agent_visibility_mode == 'assigned_only':
                all_convs = all_convs.filter(assigned_to=request.user)
            else:
                all_convs = all_convs.filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True))
                
        total_unread = all_convs.filter(panel_unread_count__gt=0).count()
        stats = {
            'total': all_convs.count(),
            'active': all_convs.filter(status__in=['activa', 'en_seguimiento', 'esperando_cliente']).count(),
            'waiting_human': all_convs.filter(status='esperando_cliente').count(),
            'finalized': all_convs.filter(status__in=['finalizada', 'archivada']).count(),
        }
        return JsonResponse({'conversations': data, 'stats': stats, 'total_unread': total_unread})


class AgentCreateView(LoginRequiredMixin, View):
    """Crear un nuevo agente/usuario."""
    login_url = '/admin/login/'

    def _get_classifications(self, business):
        try:
            return business.config.classification_categories or []
        except Exception:
            return []

    def get(self, request):
        business = Business.objects.filter(is_active=True).select_related('config').first()
        if not _user_is_admin(request.user):
            return redirect('panel:agent_list')

        context = {
            'business': business,
            'roles': UserProfile.ROLE_CHOICES,
            'classifications': self._get_classifications(business),
        }
        return render(request, 'panel/agent_form.html', context)

    def post(self, request):
        business = Business.objects.filter(is_active=True).select_related('config').first()

        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        first_name = request.POST.get('first_name', '')
        last_name = request.POST.get('last_name', '')
        role = request.POST.get('role', 'agent')
        specializations = request.POST.getlist('specializations')

        if User.objects.filter(username=username).exists():
            return render(request, 'panel/agent_form.html', {
                'business': business,
                'roles': UserProfile.ROLE_CHOICES,
                'classifications': self._get_classifications(business),
                'error': 'El nombre de usuario ya existe',
            })

        user = User.objects.create(
            username=username,
            email=email,
            password=make_password(password),
            first_name=first_name,
            last_name=last_name
        )

        UserProfile.objects.create(
            user=user,
            business=business,
            role=role,
            specializations=specializations,
        )

        return redirect('panel:agent_list')


class AgentUpdateView(LoginRequiredMixin, View):
    """Editar rol o contraseña de un agente existente."""
    login_url = '/admin/login/'

    def _get_classifications(self, business):
        try:
            return business.config.classification_categories or []
        except Exception:
            return []

    def get(self, request, user_id):
        business = Business.objects.filter(is_active=True).select_related('config').first()
        target_user = get_object_or_404(User, pk=user_id)
        target_profile = get_object_or_404(UserProfile, user=target_user, business=business)

        if not _user_is_admin(request.user) and request.user != target_user:
            return redirect('panel:agent_list')

        context = {
            'business': business,
            'target_user': target_user,
            'target_profile': target_profile,
            'roles': UserProfile.ROLE_CHOICES,
            'classifications': self._get_classifications(business),
        }
        return render(request, 'panel/agent_form.html', context)

    def post(self, request, user_id):
        business = Business.objects.filter(is_active=True).select_related('config').first()
        target_user = get_object_or_404(User, pk=user_id)
        target_profile = get_object_or_404(UserProfile, user=target_user, business=business)

        is_admin = _user_is_admin(request.user)

        target_user.first_name = request.POST.get('first_name', target_user.first_name)
        target_user.last_name = request.POST.get('last_name', target_user.last_name)
        target_user.email = request.POST.get('email', target_user.email)

        password = request.POST.get('password')
        if password:
            target_user.password = make_password(password)

        target_user.save()

        if is_admin:
            role = request.POST.get('role')
            if role:
                target_profile.role = role
            target_profile.specializations = request.POST.getlist('specializations')
            target_profile.save()

        return redirect('panel:agent_list')

@method_decorator(csrf_exempt, name='dispatch')
class AIFeedbackView(LoginRequiredMixin, View):
    """Permite a admins valorar mensajes de IA con thumbs up/down."""
    login_url = '/admin/login/'

    def post(self, request, message_id):
        if not _user_can_manage(request.user):
            return JsonResponse({'status': 'error', 'message': 'No autorizado'}, status=403)
            
        business = Business.objects.filter(is_active=True).select_related('config').first()
        if not business or not getattr(business.config, 'ai_feedback_enabled', False):
             return JsonResponse({'status': 'error', 'message': 'Feedback desactivado'}, status=403)

        message = get_object_or_404(Message, id=message_id, role='assistant')
        
        try:
            data = json.loads(request.body)
            rating = data.get('rating')
            comment = data.get('comment', '')

            if rating not in [1, -1]:
                return JsonResponse({'status': 'error', 'message': 'Calificación inválida'}, status=400)

            feedback, created = AIFeedback.objects.update_or_create(
                message=message,
                defaults={
                    'rating': rating,
                    'user': request.user,
                    'comment': comment
                }
            )
            return JsonResponse({'status': 'success', 'feedback_id': str(feedback.id)})
            
        except json.JSONDecodeError:
             return JsonResponse({'status': 'error', 'message': 'Formato inválido'}, status=400)

class PanelCreateTagView(LoginRequiredMixin, View):
    """Crear una nueva etiqueta/tag."""
    login_url = '/admin/login/'

    def post(self, request):
        if not _user_can_manage(request.user):
            return redirect('panel:settings')
            
        business = Business.objects.filter(is_active=True).first()
        from apps.conversations.models import Tag
        name = request.POST.get('name', '').strip()
        color = request.POST.get('color', '#3B82F6').strip()
        
        if business and name:
            Tag.objects.create(business=business, name=name, color=color)
            
        return redirect('/panel/settings/?saved=1')

class PanelDeleteTagView(LoginRequiredMixin, View):
    """Eliminar una etiqueta/tag."""
    login_url = '/admin/login/'

    def post(self, request, tag_id):
        if not _user_can_manage(request.user):
            return redirect('panel:settings')
            
        business = Business.objects.filter(is_active=True).first()
        from apps.conversations.models import Tag
        tag = get_object_or_404(Tag, pk=tag_id, business=business)
        tag.delete()
            
        return redirect('/panel/settings/?saved=1')
