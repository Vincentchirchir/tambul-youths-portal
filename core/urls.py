from django.contrib import admin
from django.urls import path
from .views import Index, MemberDashboardView, CommitteeDashboardView, ExportLoansCSV, ExportLoansPDF, ExportContributionsCSV, ExportContributionsPDF, ExportWelfareCSV, ExportWelfarePDF, LoanRepaymentUpdateView, LoanTotalPaidUpdateView, ContributionStatusUpdateView, ContributionAmountUpdateView, WelfareAmountUpdateView, WelfareStatusUpdateView, LoanApplicationView, LoanApprovalUpdateView, AnnouncementCreateView, PostMeetingNoteView, PostAnnouncementView, ProfileView, EditProfileView, NotificationListView, MarkNotificationReadView, MarkAllNotificationsReadView, NotificationFetchView, AnnouncementDetailView, MeetingMinutesDetailView, web_manifest, service_worker


urlpatterns = [
    path("manifest.webmanifest", web_manifest, name="web-manifest"),
    path("service-worker.js", service_worker, name="service-worker"),
    path('', Index.as_view(), name='index'),
    path('member-dashboard', MemberDashboardView.as_view(), name='member-dashboard'),
    path("committee-dashboard/", CommitteeDashboardView.as_view(), name="committee-dashboard"),
    path("export/loans/csv/", ExportLoansCSV.as_view(), name="export-loan-csv"),
    path("export/loans/pdf/", ExportLoansPDF.as_view(), name="export-loan-pdf"),
    path("export/contributions/csv/", ExportContributionsCSV.as_view(), name="export-contrib-csv"),
    path("export/contributions/pdf/", ExportContributionsPDF.as_view(), name="export-contrib-pdf"),
    path("export/welfare/csv/", ExportWelfareCSV.as_view(), name="export-welfare-csv"),
    path("export/welfare/pdf/", ExportWelfarePDF.as_view(), name="export-welfare-pdf"),
    path("loan/status/<int:pk>/<str:status>/", LoanApprovalUpdateView.as_view(), name="mark-loan-status"),
    path("loan/update/<int:pk>/<str:status>/", LoanRepaymentUpdateView.as_view(), name="mark-loan-paid"),
    path("loan/repayment/amount/<int:pk>/", LoanTotalPaidUpdateView.as_view(), name="update-loan-total-paid"),
    path("contribution/update/<int:pk>/<str:status>/", ContributionStatusUpdateView.as_view(), name="mark-contrib-status"), 
    path("contribution/amount/<int:pk>/", ContributionAmountUpdateView.as_view(), name="update-contrib-amount"),
    path("welfare/update/<int:pk>/<str:status>/", WelfareStatusUpdateView.as_view(), name="mark-welfare-status"),
    path("welfare/amount/<int:pk>/", WelfareAmountUpdateView.as_view(), name="update-welfare-amount"),
    path("apply-loan/", LoanApplicationView.as_view(), name="apply-loan"),
    path("announcement/create/", AnnouncementCreateView.as_view(), name="create-announcement"),
    path("post-minutes/", PostMeetingNoteView.as_view(), name="post-meeting-note"),
    path("post-announcement/", PostAnnouncementView.as_view(), name="post-announcement"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("profile/edit/", EditProfileView.as_view(), name="edit-profile"),
    path("notifications/", NotificationListView.as_view(), name="notifications"),
    path("notifications/read/<int:pk>/", MarkNotificationReadView.as_view(), name="mark_notification_read"),
    path("notifications/read-all/", MarkAllNotificationsReadView.as_view(), name="mark_notifications_read_all"),
    path("notifications/fetch/", NotificationFetchView.as_view(), name="notification_fetch"),
    path("announcements/<int:pk>/", AnnouncementDetailView.as_view(), name="announcement-detail"),
    path("minutes/<int:pk>/", MeetingMinutesDetailView.as_view(), name="meeting-minutes-detail"),


]
