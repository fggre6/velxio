; Force the per-user install location to %LOCALAPPDATA%\Programs\Velxio.
;
; Tauri's stock NSIS template uses $LOCALAPPDATA\<ProductName> for
; installMode=currentUser. The hook runs after SetOutPath $INSTDIR but before
; any payload is copied, so redirecting $INSTDIR and the output path here makes
; every subsequent File/CreateDirectory/shortcut/registry write use the target
; directory.

!macro NSIS_HOOK_PREINSTALL
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\Velxio"
  CreateDirectory "$INSTDIR"
  SetOutPath "$INSTDIR"
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; Remove the stock placeholder directory if it was created and is empty.
  RMDir "$LOCALAPPDATA\Velxio"
!macroend
