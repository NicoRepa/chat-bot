from django.urls import path
from . import views

app_name = 'appointments'

urlpatterns = [
    path('', views.AppointmentCalendarView.as_view(), name='calendar'),
    path('configuracion/', views.AppointmentConfigView.as_view(), name='config'),
    path('api/eventos/', views.AppointmentEventsView.as_view(), name='events'),
    path('api/crear/', views.AppointmentCreateView.as_view(), name='create'),
    path('api/<uuid:appointment_id>/', views.AppointmentDetailView.as_view(), name='detail'),
    path('api/bloquear/', views.BlockedPeriodCreateView.as_view(), name='block_create'),
    path('api/desbloquear/<int:block_id>/', views.BlockedPeriodDeleteView.as_view(), name='block_delete'),
    path('api/slots/', views.AvailableSlotsView.as_view(), name='slots'),
    path('api/horario/agregar/', views.WeeklyScheduleAddView.as_view(), name='schedule_add'),
    path('api/horario/<int:schedule_id>/eliminar/', views.WeeklyScheduleDeleteView.as_view(), name='schedule_delete'),
]
