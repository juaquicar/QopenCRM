from django import forms

from .models import Article


class ArticleForm(forms.ModelForm):
    class Meta:
        model = Article
        fields = ["title", "category", "summary", "body", "pinned", "roles"]
        widgets = {"body": forms.Textarea(attrs={"rows": 22, "spellcheck": "true"}), "roles": forms.CheckboxSelectMultiple}
