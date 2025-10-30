from django.db import models
from django.contrib.auth.models import AbstractUser, Group

class User(AbstractUser):
    ROLE_CHOICES =[
        ('admin', 'Admin'),
        ('chairperson', 'Chairperson'),
        ('committee', 'Committee'),
        ('treasurer', 'Treasurer'),
        ('secretary', 'Secretary'),
        ('coordinator', 'Coordinator'),
        ('welfare', 'Welfare'),
        ('member', 'Member'),

    ]

    email=models.EmailField(blank=True, null=True, unique=False)
    role=models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')
    phone=models.CharField(max_length=15, blank=True, null=True)
    membership_number=models.CharField(max_length=50, unique=True, null=True)
    national_id = models.CharField(max_length=20,blank=True, null=True)


    USERNAME_FIELD='username'
    REQUIRED_FIELDS=[]

    def __str__(self):
        full_name=f"{self.first_name} {self.last_name}".strip()
        if full_name:
            return f"{full_name} ({self.username})"
        return self.username

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        try:
            group=Group.objects.get(name__iexact=self.role.capitalize())
            self.groups.clear()
            self.groups.add(group)
        except Group.DoesNotExist:
            pass
    
    
