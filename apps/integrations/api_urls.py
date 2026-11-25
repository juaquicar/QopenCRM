from django.urls import path
from rest_framework.routers import DefaultRouter

from . import api

router = DefaultRouter()
router.register("companies", api.CompanyViewSet)
router.register("contacts", api.ContactViewSet)
router.register("opportunities", api.OpportunityViewSet)
router.register("opportunity-lines", api.LineViewSet)
router.register("activities", api.ActivityViewSet)
router.register("tasks", api.TaskViewSet)
router.register("products", api.ProductViewSet)
router.register("product-families", api.FamilyViewSet)
router.register("stages", api.StageViewSet)
router.register("loss-reasons", api.LossReasonViewSet)
router.register("subscriptions", api.SubscriptionViewSet)
router.register("hour-banks", api.HourBankViewSet)
router.register("hour-entries", api.HourEntryViewSet)
router.register("quotes", api.QuoteViewSet)
router.register("users", api.UserViewSet)

urlpatterns = [
    path("me/", api.MeView.as_view()),
    path("reports/", api.ReportView.as_view()),
    path("export/", api.ExportView.as_view()),
    *router.urls,
]
