from django.shortcuts import render
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy

class UserLoginView(LoginView):
    template_name='accounts/login.html'
    redirect_authenticated_user=True

    def get_success_url(self):
        user=self.request.user
        if user.is_superuser or user.email=='tambulhustleyouthgroup@gmailcom':
            return reverse_lazy('admin:index')
        if user.role in ["chairperson", "treasurer", "secretary", "welfare", "coordinator"]:
            return reverse_lazy('committee-dashboard')
        return reverse_lazy('member-dashboard')
    
class UserLogoutView(LogoutView):
    next_page=reverse_lazy('index')


