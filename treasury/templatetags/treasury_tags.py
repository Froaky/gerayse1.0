
from decimal import Decimal
from django import template

register = template.Library()

@register.filter(name='money')
def money(value):
    if value is None:
        value = Decimal("0.00")
    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except:
            return "$ 0,00"
    
    formatted = f"{value:,.2f}"
    # Replace , with _ (temp), . with , (final decimal), _ with . (final thousands)
    return f"$ {formatted.replace(',', '_').replace('.', ',').replace('_', '.')}"
