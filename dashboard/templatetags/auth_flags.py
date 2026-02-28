from django import template
from django.urls import NoReverseMatch, reverse

from dashboard.setup_state import (
    is_asana_connector_configured,
    is_github_connector_configured,
    is_microsoft_connector_configured,
)

register = template.Library()


@register.simple_tag
def microsoft_login_available() -> bool:
    return is_microsoft_connector_configured()


@register.simple_tag
def microsoft_login_url() -> str:
    try:
        return reverse("microsoft_login")
    except NoReverseMatch:
        return "/accounts/microsoft/login/"


@register.simple_tag
def github_login_available() -> bool:
    return is_github_connector_configured()


@register.simple_tag
def github_login_url() -> str:
    try:
        return reverse("github_login")
    except NoReverseMatch:
        return "/accounts/github/login/"


@register.simple_tag
def asana_login_available() -> bool:
    return is_asana_connector_configured()


@register.simple_tag
def asana_login_url() -> str:
    try:
        return reverse("asana_login")
    except NoReverseMatch:
        return "/accounts/asana/login/"
