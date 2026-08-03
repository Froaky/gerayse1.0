
from decimal import Decimal
from django import template

register = template.Library()

@register.filter(name='money')
def money(value):
    # Un solo formato de plata en todo el sistema: services.formato_money.
    from treasury.services import formato_money

    return formato_money(value)
