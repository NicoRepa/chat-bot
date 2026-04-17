import json
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.management import call_command
from django.http import HttpResponse
from django.core import serializers
from django.db import transaction, IntegrityError
import io

@user_passes_test(lambda u: u.is_superuser)
def backup_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'export':
            try:
                # Create an in-memory string buffer to hold the output
                out = io.StringIO()
                # Exclude tables that shouldn't be backed up usually (logs, sessions)
                # You can adjust the excludes based on requirements.
                call_command(
                    'dumpdata', 
                    exclude=['contenttypes', 'auth.permission', 'admin.logentry', 'sessions.session'],
                    format='json',
                    indent=2,
                    stdout=out
                )
                
                response = HttpResponse(out.getvalue(), content_type='application/json')
                response['Content-Disposition'] = 'attachment; filename="backup_chatbot.json"'
                return response
            except Exception as e:
                messages.error(request, f"Error al generar el backup: {str(e)}")
                return redirect('admin_backup')
                
        elif action == 'import':
            if 'backup_file' not in request.FILES:
                messages.error(request, "Debes subir un archivo JSON.")
                return redirect('admin_backup')
                
            backup_file = request.FILES['backup_file']
            if not backup_file.name.endswith('.json'):
                messages.error(request, "El archivo debe ser un JSON de backup válido.")
                return redirect('admin_backup')
                
            try:
                file_data = backup_file.read().decode('utf-8')
                
                # We use deserialize which yields DeserializedObject instances
                # We only save them if they don't already exist in the database.
                objects_added = 0
                objects_skipped = 0
                
                with transaction.atomic():
                    for obj in serializers.deserialize('json', file_data):
                        # obj.object is the actual model instance
                        # obj.object.__class__ gets the Model class
                        ModelClass = obj.object.__class__
                        pk = obj.object.pk
                        
                        # Check if it exists
                        if ModelClass.objects.filter(pk=pk).exists():
                            objects_skipped += 1
                        else:
                            try:
                                obj.save()
                                objects_added += 1
                            except IntegrityError as e:
                                # In case of other constraint violations
                                objects_skipped += 1
                                
                messages.success(request, f"Importación completada: {objects_added} registros insertados nuevos, {objects_skipped} omitidos (ya existían o hubo error).")
            except Exception as e:
                messages.error(request, f"Error procesando el archivo de importación: {str(e)}")
                
            return redirect('admin_backup')

    return render(request, 'admin/backup.html', {
        'title': 'Sistema de Backup y Restauración'
    })
