from django.urls import path
from . import views
from .push_views import PushSubscribeView

app_name = 'panel'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('conversaciones/', views.ConversationListView.as_view(), name='conversation_list'),
    path('conversaciones/exportar/csv/', views.PanelExportCSVView.as_view(), name='export_csv'),
    path('conversaciones/actualizaciones/', views.PanelConversationUpdatesView.as_view(), name='conversation_updates'),
    path(
        'conversaciones/<uuid:conversation_id>/',
        views.ConversationDetailView.as_view(),
        name='conversation_detail'
    ),
    path(
        'conversaciones/<uuid:conversation_id>/responder/',
        views.PanelReplyView.as_view(),
        name='reply'
    ),
    path(
        'conversaciones/<uuid:conversation_id>/toggle-ai/',
        views.PanelToggleAIView.as_view(),
        name='toggle_ai'
    ),
    path(
        'conversaciones/<uuid:conversation_id>/estado/',
        views.PanelUpdateStatusView.as_view(),
        name='update_status'
    ),
    path(
        'conversaciones/<uuid:conversation_id>/clasificar/',
        views.PanelUpdateClassificationView.as_view(),
        name='update_classification'
    ),
    path(
        'conversaciones/<uuid:conversation_id>/etiqueta/toggle/',
        views.PanelToggleTagView.as_view(),
        name='toggle_tag'
    ),
    path(
        'conversaciones/<uuid:conversation_id>/resumen/',
        views.PanelRefreshSummaryView.as_view(),
        name='refresh_summary'
    ),
    path(
        'conversaciones/<uuid:conversation_id>/reenviar-menu/',
        views.PanelResendMenuView.as_view(),
        name='resend_menu'
    ),
    path(
        'conversaciones/<uuid:conversation_id>/mensajes/',
        views.PanelMessagesView.as_view(),
        name='get_messages'
    ),
    path(
        'conversaciones/<uuid:conversation_id>/asignar/',
        views.PanelAssignAgentView.as_view(),
        name='assign_agent'
    ),
    path(
        'conversaciones/mensajes/<uuid:message_id>/feedback/',
        views.AIFeedbackView.as_view(),
        name='ai_feedback'
    ),
    # Mini-CRM
    path('contactos/', views.ContactListView.as_view(), name='contact_list'),
    path('contactos/<uuid:contact_id>/', views.ContactDetailView.as_view(), name='contact_detail'),
    
    # User Management
    path('agentes/', views.AgentListView.as_view(), name='agent_list'),
    path('agentes/nuevo/', views.AgentCreateView.as_view(), name='agent_create'),
    path('agentes/<int:user_id>/editar/', views.AgentUpdateView.as_view(), name='agent_update'),
    path('menu/', views.MenuConfigView.as_view(), name='menu_config'),
    # Menu CRUD
    path('menu/categorias/crear/', views.MenuCategoryCreateView.as_view(), name='menu_category_create'),
    path('menu/categorias/<uuid:category_id>/editar/', views.MenuCategoryUpdateView.as_view(), name='menu_category_update'),
    path('menu/categorias/<uuid:category_id>/eliminar/', views.MenuCategoryDeleteView.as_view(), name='menu_category_delete'),
    path('menu/subcategorias/crear/', views.MenuSubcategoryCreateView.as_view(), name='menu_subcategory_create'),
    path('menu/subcategorias/<uuid:subcategory_id>/editar/', views.MenuSubcategoryUpdateView.as_view(), name='menu_subcategory_update'),
    path('menu/subcategorias/<uuid:subcategory_id>/eliminar/', views.MenuSubcategoryDeleteView.as_view(), name='menu_subcategory_delete'),
    path('menu/subsubcategorias/crear/', views.MenuSubSubcategoryCreateView.as_view(), name='menu_subsubcategory_create'),
    path('menu/subsubcategorias/<uuid:item_id>/editar/', views.MenuSubSubcategoryUpdateView.as_view(), name='menu_subsubcategory_update'),
    path('menu/subsubcategorias/<uuid:item_id>/eliminar/', views.MenuSubSubcategoryDeleteView.as_view(), name='menu_subsubcategory_delete'),
    path('simulador/', views.SimulatorView.as_view(), name='simulator'),
    path('settings/', views.SettingsView.as_view(), name='settings'),
    path('push/subscribe/', PushSubscribeView.as_view(), name='push_subscribe'),
    path('etiquetas/crear/', views.PanelCreateTagView.as_view(), name='tag_create'),
    path('etiquetas/<uuid:tag_id>/eliminar/', views.PanelDeleteTagView.as_view(), name='tag_delete'),
]
