from django.contrib import admin

from .models import Product, ProductFamily

for model in (Product, ProductFamily):
    admin.site.register(model)
