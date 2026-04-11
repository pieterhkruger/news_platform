"""
Form classes for account authentication, registration, and profile management.

Classes:
- RegistrationForm: Self-service sign-up for all four roles; applies
  role-specific validation and creates linked model rows on save.
- PasswordResetRequestForm: Password-reset request using the project's
  email fallback logic instead of Django's default transport.
- PasswordResetConfirmForm: Two-field new-password confirmation using
  Django's built-in validators.
- ProfileUpdateForm: Signed-in user edits their public profile with
  role-specific field rules and username auto-sync on name changes.
"""

from django import forms
# To get the active custom user model from Django settings.
from django.contrib.auth import get_user_model  # Cf. Mele (2025:372)
# PasswordResetForm locates matching active users and renders the built-in
# password-reset templates.  SetPasswordForm provides two password fields +
# Django's built-in validators (min length, common passwords, similarity to
# username, etc.).  UserCreationForm extends SetPasswordForm with a username
# field and the unique-username check — the standard base class for
# registration forms.
# Cf.: https://docs.djangoproject.com/en/5.2/topics/auth/default/#built-in-forms
from django.contrib.auth.forms import (
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.template import loader
from django.utils.text import slugify

from .utils import send_password_reset_email

# Cf. Mele (2025:373). get_user_model() returns the custom user model used
# in this project to distinguish between reader, journalist, editor, and
# publisher accounts.
User = get_user_model()


# =============================================================================
# Helpers: Username generation
# =============================================================================
def _build_unique_editor_username(*, first_name, last_name, exclude_pk=None):
    """
    Generate a unique login username from an editor's real name.

    Editors must present a real-name identity publicly, so self-service
    account flows derive the username from first_name + last_name instead of
    trusting a freely chosen handle. A numeric suffix is appended when the
    base username is already taken.
    """
    joined_name = " ".join(
        part.strip() for part in (first_name, last_name) if part and part.strip()
    )
    base_username = slugify(joined_name).replace("-", ".").strip(".") or "editor"

    # Build the base QuerySet of all existing users whose usernames must not
    # collide with the generated editor username.
    queryset = User.objects.all()
    if exclude_pk is not None:
        # Add WHERE pk != exclude_pk when editing an existing user.
        queryset = queryset.exclude(pk=exclude_pk)

    username = base_username[:150]
    counter = 2
    # Rebuild the queryset with WHERE username = username each loop, then
    # determine whether a matching user row already exists.
    while queryset.filter(username=username).exists():
        suffix = f".{counter}"
        trimmed_base = base_username[: max(1, 150 - len(suffix))]
        username = f"{trimmed_base}{suffix}"
        counter += 1

    return username


def _build_unique_publisher_username(*, publisher_name, exclude_pk=None):
    """
    Generate a unique login username from a publisher's organisation name.

    Publisher accounts represent organisations, so the login username is
    derived from the publisher name instead of a free-form handle.
    """
    base_username = (
        slugify((publisher_name or "").strip()).replace("-", ".").strip(".")
        or "publisher"
    )

    # Build the base QuerySet of all existing users whose usernames must not
    # collide with the generated publisher username.
    queryset = User.objects.all()
    if exclude_pk is not None:
        # Add WHERE pk != exclude_pk when editing an existing user.
        queryset = queryset.exclude(pk=exclude_pk)

    username = base_username[:150]
    counter = 2
    # Rebuild the queryset with WHERE username = username each loop, then
    # determine whether a matching user row already exists.
    while queryset.filter(username=username).exists():
        suffix = f".{counter}"
        trimmed_base = base_username[: max(1, 150 - len(suffix))]
        username = f"{trimmed_base}{suffix}"
        counter += 1

    return username


# =============================================================================
# Form: Registration (all user types)
# =============================================================================
# CORE REQUIREMENT - Self-service registration for Reader, Journalist, and
# Editor accounts, including Django password validation and role capture.
# =============================================================================
class RegistrationForm(UserCreationForm):
    """
    Register a reader, journalist, editor, or publisher account with Django's
    password
    validators.

    The role field drives group assignment after the user is saved (see
    ``User.assign_to_role_group``). Reader and journalist accounts may choose
    their own usernames, while editor and publisher accounts receive generated
    login usernames derived from their public identities. Publisher accounts
    represent organisations, so they do not collect personal first/last names;
    the organisation name is stored in ``display_name`` instead.
    """

    first_name = forms.CharField(required=False, max_length=150)
    last_name = forms.CharField(required=False, max_length=150)
    email = forms.EmailField(required=True)
    role = forms.ChoiceField(
        choices=User.ROLE_CHOICES,
        initial="reader",
        widget=forms.RadioSelect,
    )
    publisher_name = forms.CharField(
        required=False,
        max_length=200,
        label="Publisher name",
    )
    publisher_description = forms.CharField(
        required=False,
        label="Publisher description",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    accept_terms = forms.BooleanField(
        required=True,
        label=(
            "I have read and agree to the terms and conditions"
            " applicable to my selected role"
        ),
        error_messages={
            "required": (
                "You must accept the terms and conditions to register."
            )
        },
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            "first_name",
            "last_name",
            "username",
            "email",
            "role",
            "password1",
            "password2",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        selected_role = (
            self.data.get("role")
            or self.initial.get("role")
            or self.fields["role"].initial
            or "reader"
        )
        if selected_role == "editor":
            self.fields["username"].help_text = (
                "Editor usernames are generated from your real name and used "
                "for login only."
            )
        elif selected_role == "publisher":
            self.fields["username"].help_text = (
                "Publisher usernames are generated from the organisation name "
                "and used for login only."
            )
        else:
            self.fields["username"].help_text = (
                "Used when you sign in. Editor and publisher usernames are "
                "generated from their public identities."
            )
        if selected_role in {"editor", "publisher"}:
            self.fields["username"].required = False

    def clean(self):
        """
        Require a real-name identity for editor self-registration.
        """
        cleaned_data = super().clean()
        role = cleaned_data.get("role")
        first_name = cleaned_data.get("first_name", "").strip()
        last_name = cleaned_data.get("last_name", "").strip()
        publisher_name = cleaned_data.get("publisher_name", "").strip()

        if role == "editor":
            if not first_name:
                self.add_error(
                    "first_name",
                    "Editors must register with their real first name.",
                )
            if not last_name:
                self.add_error(
                    "last_name",
                    "Editors must register with their real last name.",
                )

            cleaned_data["username"] = _build_unique_editor_username(
                first_name=first_name or "editor",
                last_name=last_name or "user",
            )
        elif role == "publisher":
            from daily_indaba.models import Publisher

            if not publisher_name:
                self.add_error(
                    "publisher_name",
                    "Publishers must register the organisation name.",
                )
            else:
                # Build a queryset with WHERE lower(name) = lower(the submitted
                # publisher_name),
                # then return the first matching Publisher row or None.
                existing_publisher = Publisher.objects.filter(
                    name__iexact=publisher_name
                ).first()
                if existing_publisher and existing_publisher.account_id:
                    self.add_error(
                        "publisher_name",
                        "A publisher account already exists for this organisation.",
                    )

            cleaned_data["publisher_name"] = publisher_name
            cleaned_data["publisher_description"] = cleaned_data.get(
                "publisher_description", ""
            ).strip()
            cleaned_data["first_name"] = ""
            cleaned_data["last_name"] = ""
            cleaned_data["username"] = _build_unique_publisher_username(
                publisher_name=publisher_name or "publisher",
            )

        return cleaned_data

    def clean_email(self):
        """
        Treat e-mail addresses as a unique self-service identity.

        Django's default user model does not require unique emails, but this
        project uses email for password-reset and account recovery.  Rejecting
        duplicates during registration keeps that recovery path unambiguous.
        """
        email = self.cleaned_data["email"].strip()
        # Build a queryset with WHERE lower(email) = lower(the submitted email), then
        # determine whether a matching user row already exists.
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "An account with that email address already exists."
            )
        return email

    def save(self, commit=True):
        # super().save(commit=False) creates the User object in memory WITHOUT
        # writing it to the database yet, giving us a chance to set extra fields
        # (email, role) before the INSERT happens.  commit=True then calls
        # user.save() to persist the row.
        # Cf.: https://docs.djangoproject.com/en/5.2/topics/forms/modelforms/#the-save-method
        user = super().save(commit=False)
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        user.email = self.cleaned_data["email"]
        user.role = self.cleaned_data["role"]
        if user.role in {"editor", "publisher"}:
            user.username = self.cleaned_data["username"]
        if user.role == "editor":
            user.display_name = ""
        elif user.role == "publisher":
            user.first_name = ""
            user.last_name = ""
            user.display_name = self.cleaned_data["publisher_name"]
        if commit:
            user.save()
            if user.role == "publisher":
                from daily_indaba.models import Publisher

                publisher_name = self.cleaned_data["publisher_name"]
                publisher_description = self.cleaned_data[
                    "publisher_description"
                ]
                # Build a queryset with WHERE lower(name) = lower(the submitted
                # publisher_name),
                # then return the first matching Publisher row or None.
                publisher = Publisher.objects.filter(
                    name__iexact=publisher_name
                ).first()
                if publisher is None:
                    # Insert a new Publisher row only when the prior lookup
                    # found no matching organisation name.
                    publisher = Publisher.objects.create(
                        name=publisher_name,
                        description=publisher_description,
                        account=user,
                    )
                publisher.account = user
                if publisher_description:
                    publisher.description = publisher_description
                publisher.save()
        return user


# =============================================================================
# Form: Password reset request
# =============================================================================
# CORE REQUIREMENT - Password recovery by email using Django's built-in reset
# token flow, as required by the capstone brief's account-recovery pathway.
# =============================================================================
class PasswordResetRequestForm(PasswordResetForm):
    """
    Reuse Django's built-in reset form while keeping the project's e-mail
    fallback helper.

    ``PasswordResetView`` still decides which users are eligible for reset
    (active accounts with usable passwords).  This subclass only intercepts
    the rendered subject/body so the project can route them through
    ``accounts.utils.send_password_reset_email``.
    """

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        # Django renders the subject/body templates before calling send_mail().
        # We keep that standard flow, then delegate the actual transport to the
        # project's helper so SMTP fallback stays consistent.
        subject = loader.render_to_string(subject_template_name, context)
        # Email subject headers must be a single physical line.
        subject = "".join(subject.splitlines())
        body = loader.render_to_string(email_template_name, context)
        html_body = None
        if html_email_template_name is not None:
            html_body = loader.render_to_string(
                html_email_template_name,
                context,
            )

        send_password_reset_email(
            user=context["user"],
            subject=subject,
            body=body,
            to_email=to_email,
            from_email=from_email,
            html_body=html_body,
        )


# =============================================================================
# Form: Password reset confirm
# =============================================================================
class PasswordResetConfirmForm(SetPasswordForm):
    """
    Reuse Django's built-in password validation and confirmation flow.
    """


# =============================================================================
# Form: Profile update
# =============================================================================
# DERIVED REQUIREMENT - Enforce role-specific identity rules during profile
# editing, especially the editor real-name rule and editor-logo-only upload
# confirmation, instead of relying on template hints alone.
# =============================================================================
class ProfileUpdateForm(forms.ModelForm):
    """
    Update the signed-in user's public profile information.

    The ``role`` field is intentionally excluded — a user cannot change
    their own role through self-service; that is an administrative action.

    ``display_name`` is included for readers and journalists only.
    Editors may not set a pseudonym (requirement §2.3); the field is
    hidden for editor accounts via ``__init__``.
    Editors must also keep real first/last names on file, and any uploaded
    image must be confirmed as an official organisation or publication logo.
    """

    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "email",
            "display_name",
            "bio",
            "profile_picture",
        )
        labels = {
            "first_name": "First name",
            "last_name": "Last name",
            "email": "Email address",
            "display_name": "Display name / pseudonym (optional)",
            "bio": "Bio",
            "profile_picture": "Profile picture",
        }
        widgets = {
            "first_name": forms.TextInput(
                attrs={"class": "form-control"}
            ),
            "last_name": forms.TextInput(
                attrs={"class": "form-control"}
            ),
            "email": forms.EmailInput(
                attrs={"class": "form-control"}
            ),
            "display_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": (
                        "Leave blank to show your username publicly."
                    ),
                }
            ),
            "bio": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
        }
        help_texts = {
            "display_name": (
                "Shown instead of your username in comments and "
                "by-lines. Not available to editors."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Editors must use their real identity — hide the field entirely.
        # self.fields is an OrderedDict of field objects keyed by field name.
        # .pop() removes the key so the field never renders in the form HTML
        # and cannot be submitted by the browser at all.
        # Cf.: https://docs.djangoproject.com/en/5.2/ref/forms/api/#django.forms.Form.fields
        instance = kwargs.get("instance")
        if instance and instance.role == "editor":
            self.fields.pop("display_name")
        if instance and instance.role == "editor":
            self.fields["first_name"].required = True
            self.fields["last_name"].required = True
            self.fields["profile_picture"].label = (
                "Organisation / publication logo"
            )
            self.fields["profile_picture"].help_text = (
                "Editors may upload only an official organisation or "
                "publication logo. Personal avatars are not permitted."
            )
            self.fields["editor_logo_confirmation"] = forms.BooleanField(
                required=False,
                label=(
                    "I confirm that this image is an official organisation "
                    "or publication logo."
                ),
            )
        elif instance and instance.role == "publisher":
            self.fields.pop("first_name")
            self.fields.pop("last_name")
            self.fields["display_name"].label = "Publisher name"
            self.fields["display_name"].help_text = (
                "Organisation name shown publicly for this publisher account."
            )
            self.fields["display_name"].widget.attrs["placeholder"] = (
                "Enter the publisher name."
            )
            self.fields["profile_picture"].label = (
                "Organisation / publication logo"
            )
            self.fields["profile_picture"].help_text = (
                "Optional logo used for the publisher account."
            )

    def clean_display_name(self):
        """Prevent editors from setting a display name."""
        # Developer's note: Django calls clean_<fieldname>() automatically
        # during form validation for each field with such a method defined.
        # Returning "" here overrides whatever the browser submitted, providing
        # a server-side safety net even if the field was hidden client-side.
        # Cf.: https://docs.djangoproject.com/en/5.2/ref/forms/validation/#cleaning-a-specific-field-attribute
        instance = getattr(self, "instance", None)
        if instance and instance.role == "editor":
            return ""
        value = self.cleaned_data.get("display_name", "").strip()
        if instance and instance.role == "publisher" and not value:
            raise forms.ValidationError(
                "Publishers must keep the organisation name on file."
            )
        return value

    def clean_email(self):
        """
        Prevent two accounts from claiming the same e-mail via self-service
        profile edits.
        """
        email = self.cleaned_data["email"].strip()
        duplicate = (
            # Build a queryset with WHERE lower(email) = lower(the submitted
            # email) while
            # excluding the current user's own row.
            User.objects.filter(email__iexact=email)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if duplicate:
            raise forms.ValidationError(
                "Another account already uses that email address."
            )
        return email

    def clean(self):
        """
        Enforce editor identity and logo rules during profile edits.
        """
        cleaned_data = super().clean()
        instance = getattr(self, "instance", None)
        if not instance or instance.role not in {"editor", "publisher"}:
            return cleaned_data

        if instance.role == "publisher":
            publisher_name = cleaned_data.get("display_name", "").strip()
            publisher = getattr(instance, "managed_publisher", None)
            from daily_indaba.models import Publisher

            # Build a queryset with WHERE lower(name) = lower(the submitted
            # publisher_name).
            duplicate = Publisher.objects.filter(name__iexact=publisher_name)
            if publisher is not None:
                # Exclude the current publisher row when editing an existing
                # publisher account.
                duplicate = duplicate.exclude(pk=publisher.pk)
            # Determine whether any other publisher already uses this name.
            if publisher_name and duplicate.exists():
                self.add_error(
                    "display_name",
                    "Another publisher already uses that organisation name.",
                )
            cleaned_data["display_name"] = publisher_name
            cleaned_data["first_name"] = ""
            cleaned_data["last_name"] = ""
            cleaned_data["username"] = _build_unique_publisher_username(
                publisher_name=publisher_name or instance.display_name or "publisher",
                exclude_pk=instance.pk,
            )
            return cleaned_data

        cleaned_data["display_name"] = ""

        first_name = cleaned_data.get("first_name", "").strip()
        last_name = cleaned_data.get("last_name", "").strip()

        if not first_name:
            self.add_error(
                "first_name",
                "Editors must keep their real first name on file.",
            )
        if not last_name:
            self.add_error(
                "last_name",
                "Editors must keep their real last name on file.",
            )

        if (
            cleaned_data.get("profile_picture")
            and "profile_picture" in self.changed_data
            and not cleaned_data.get("editor_logo_confirmation")
        ):
            self.add_error(
                "editor_logo_confirmation",
                "Confirm that the selected image is an official logo.",
            )

        cleaned_data["username"] = _build_unique_editor_username(
            first_name=first_name or instance.first_name or "editor",
            last_name=last_name or instance.last_name or "user",
            exclude_pk=instance.pk,
        )
        return cleaned_data

    def save(self, commit=True):
        """
        Persist the profile update while keeping editor identity fields aligned.
        """
        user = super().save(commit=False)
        self.editor_username_changed = False
        self.editor_username = user.username

        if user.role == "editor":
            generated_username = self.cleaned_data["username"]
            self.editor_username_changed = generated_username != user.username
            self.editor_username = generated_username
            user.username = generated_username
            user.display_name = ""
        elif user.role == "publisher":
            generated_username = self.cleaned_data["username"]
            user.username = generated_username
            user.first_name = ""
            user.last_name = ""
            user.display_name = self.cleaned_data["display_name"]

        if commit:
            user.save()
            if user.role == "publisher":
                publisher = getattr(user, "managed_publisher", None)
                if publisher is not None:
                    publisher.name = user.display_name
                    publisher.save(update_fields=["name"])
        return user
