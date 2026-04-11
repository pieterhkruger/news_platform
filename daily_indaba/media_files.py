"""Runtime helpers for cleaning up replaced or deleted article media files.

Purpose:
    Django stores uploaded file names in database fields, but the actual files
    live separately in storage. When an ``Article.image`` value changes or an
    ``Article`` row is deleted, Django does not automatically remove the old
    file from storage.

Why this is necessary:
    The normal runtime save/delete flow needs one safe cleanup helper that can
    answer a precise question: does any remaining database row still reference
    this stored file? If the answer is no, the file can be deleted.

Functions:
    ``_resolve_field(model, field_name)``
        Return the Django field object for the supplied file/image field name.
    ``delete_field_file_if_unreferenced(model, field_name, file_name, exclude_pk=None)``
        Delete one stored file only if no remaining row still points at it.
"""


def _resolve_field(model, field_name):
    """Return the concrete Django field object for *field_name* on *model*.

    The returned field exposes the storage backend and default ``upload_to``
    prefix used by the file/image field.

    Example:
        ``_resolve_field(Article, "image")`` returns the ``Article.image``
        field object, which then gives access to attributes such as
        ``field.storage`` and ``field.upload_to``.
    """
    return model._meta.get_field(field_name)


# ---------------------------------------------------------------------------
# Normal runtime cleanup: used by Article save/delete hooks
# ---------------------------------------------------------------------------
# This helper is part of the ordinary application flow. ``Article.save()`` and
# ``Article.delete()`` call it after image replacements/deletions so runtime
# changes can prune an old file immediately when no row still uses it.

def delete_field_file_if_unreferenced(model,
                                      field_name,
                                      file_name,
                                      exclude_pk=None
                                      ):
    """Delete one file only when no remaining row still references it.

    This targeted helper is used when one model row changes or is deleted. It
    checks whether the old stored file name is still referenced elsewhere and
    only removes the file if the database says it is truly orphaned.
    """
    # Empty file names represent "no uploaded file", so there is nothing to
    # delete and the helper can return immediately.
    if not file_name:
        return False

    field = _resolve_field(model, field_name)
    # Build a queryset of rows that still point at this stored file name by
    # unpacking the dictionary into named keyword arguments for Django's ORM.
    referencing_rows = model.objects.filter(
        **{field_name: file_name}
    )
    if exclude_pk is not None:
        # Optionally exclude one specific row when the caller only wants to
        # know whether any other rows still reference the same stored file.
        referencing_rows = referencing_rows.exclude(pk=exclude_pk)
    # If any row still references the file, leave the storage object alone.
    if referencing_rows.exists():
        return False

    # Otherwise the file is orphaned, so delete it from storage.
    field.storage.delete(file_name)
    return True
