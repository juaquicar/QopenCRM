from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from apps.accounts.models import AccessLog, log_access

from .forms import ArticleForm
from .models import Article
from .render import to_html


def _manager_or_403(user):
    if not user.can_manage_knowledge:
        raise PermissionDenied


def article_list(request):
    """Índice por categorías, con buscador en título, resumen y contenido."""
    q = request.GET.get("q", "").strip()
    articles = Article.visible_to(request.user)
    for word in q.split():
        articles = articles.filter(Q(title__icontains=word) | Q(summary__icontains=word) | Q(body__icontains=word) | Q(category__icontains=word))
    groups = {}
    for article in articles:
        groups.setdefault(article.category, []).append(article)
    return render(request, "knowledge/list.html", {"groups": groups, "q": q, "total": sum(len(items) for items in groups.values())})


def article_detail(request, slug):
    article = get_object_or_404(Article.visible_to(request.user), slug=slug)
    return render(request, "knowledge/detail.html", {"article": article, "html": mark_safe(to_html(article.body)),
                                                     "restricted": list(article.roles.all())})


def article_edit(request, slug=None):
    _manager_or_403(request.user)
    article = get_object_or_404(Article, slug=slug) if slug else None
    form = ArticleForm(request.POST or None, instance=article)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.updated_by = request.user
        saved.save()
        form.save_m2m()
        log_access(request, AccessLog.Action.UPDATE if article else AccessLog.Action.CREATE, saved)
        messages.success(request, "Documento guardado.")
        return redirect("article", slug=saved.slug)
    return render(request, "knowledge/edit.html", {"form": form, "article": article})


@require_POST
def article_delete(request, slug):
    _manager_or_403(request.user)
    article = get_object_or_404(Article, slug=slug)
    log_access(request, AccessLog.Action.DELETE, article)
    article.delete()
    messages.success(request, "Documento borrado.")
    return redirect("articles")
