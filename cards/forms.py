from django import forms

from .models import CardImage

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


class CardImageUploadForm(forms.ModelForm):
    # Override to control error messages; Django's ImageField uses PIL to verify
    # the file is a real image, so no additional content-type sniffing is needed.
    image = forms.ImageField(
        error_messages={
            "required": "Please select an image.",
            "invalid_image": "The uploaded file is not a valid image.",
        }
    )

    class Meta:
        model = CardImage
        fields = ["image"]

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image and image.size > MAX_UPLOAD_BYTES:
            limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
            raise forms.ValidationError(f"File too large. Maximum allowed size is {limit_mb} MB.")
        return image
