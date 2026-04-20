from django.contrib import admin
from .models import AppointmentConfig, WeeklyAvailability, BlockedPeriod, Appointment


class WeeklyAvailabilityInline(admin.TabularInline):
    model = WeeklyAvailability
    extra = 0
    fields = ('day_of_week', 'start_time', 'end_time', 'is_active')


class BlockedPeriodInline(admin.TabularInline):
    model = BlockedPeriod
    extra = 0
    fields = ('date', 'is_full_day', 'start_time', 'end_time', 'reason')


@admin.register(AppointmentConfig)
class AppointmentConfigAdmin(admin.ModelAdmin):
    list_display = ('business', 'is_enabled', 'slot_name', 'appointment_duration', 'max_concurrent', 'max_per_day')
    list_filter = ('is_enabled',)
    inlines = [WeeklyAvailabilityInline, BlockedPeriodInline]


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ('contact_name', 'contact_phone', 'start_datetime', 'end_datetime', 'status', 'created_by_ai')
    list_filter = ('status', 'created_by_ai', 'config__business')
    search_fields = ('contact_name', 'contact_phone', 'notes')
    readonly_fields = ('id', 'created_at', 'updated_at')
    list_select_related = ('config__business',)
    ordering = ('-start_datetime',)


@admin.register(BlockedPeriod)
class BlockedPeriodAdmin(admin.ModelAdmin):
    list_display = ('date', 'is_full_day', 'start_time', 'end_time', 'reason', 'config')
    list_filter = ('is_full_day', 'config__business')
    ordering = ('-date',)
