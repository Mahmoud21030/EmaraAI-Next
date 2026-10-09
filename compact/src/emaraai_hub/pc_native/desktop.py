"""Native Windows UI automation for the Local PC Bridge (the `ui` tool).

Uses Windows' own UI Automation through PowerShell (System.Windows.Automation ships with Windows),
so there is nothing to install. Every action is one generated script: a fixed prelude + one action block.
All values coming from the chat travel as base64 JSON, never as script text.
"""
from __future__ import annotations

import base64
import json

PRELUDE = r'''
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms, System.Drawing
$AE=[System.Windows.Automation.AutomationElement]; $TS=[System.Windows.Automation.TreeScope]; $root=$AE::RootElement
$TRUE_COND=[System.Windows.Automation.Condition]::TrueCondition
$A = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__ARGS__')) | ConvertFrom-Json
function Cond($s){
  $c=New-Object System.Collections.Generic.List[System.Windows.Automation.Condition]
  if($s.name){$c.Add((New-Object System.Windows.Automation.PropertyCondition($AE::NameProperty,[string]$s.name)))}
  if($s.automation_id){$c.Add((New-Object System.Windows.Automation.PropertyCondition($AE::AutomationIdProperty,[string]$s.automation_id)))}
  if($s.process_id){$c.Add((New-Object System.Windows.Automation.PropertyCondition($AE::ProcessIdProperty,[int]$s.process_id)))}
  if($s.control_type){
    $ct=[System.Windows.Automation.ControlType]
    $map=@{button=$ct::Button;edit=$ct::Edit;text=$ct::Text;document=$ct::Document;pane=$ct::Pane;window=$ct::Window;menuitem=$ct::MenuItem;checkbox=$ct::CheckBox;combobox=$ct::ComboBox;listitem=$ct::ListItem}
    $c.Add((New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty,$map[[string]$s.control_type])))
  }
  if($c.Count -eq 0){return $TRUE_COND}
  if($c.Count -eq 1){return $c[0]}
  return New-Object System.Windows.Automation.AndCondition -ArgumentList (,$c.ToArray())
}
function Find-Target($s){
  if(-not $s){return $root}
  if($s.within){ $scope=Find-Target $s.within; $el=$scope.FindFirst($TS::Descendants,(Cond $s)) }
  else { $el=$root.FindFirst($TS::Children,(Cond $s)); if(-not $el){ $el=$root.FindFirst($TS::Descendants,(Cond $s)) } }
  if(-not $el){ throw ("target not found: " + ($s | ConvertTo-Json -Compress -Depth 6)) }
  return $el
}
function Info($e){ [pscustomobject]@{name=$e.Current.Name;automation_id=$e.Current.AutomationId;control_type=($e.Current.ControlType.ProgrammaticName -replace 'ControlType\.','').ToLower();class_name=$e.Current.ClassName;process_id=$e.Current.ProcessId;enabled=$e.Current.IsEnabled} }
Add-Type -Namespace W -Name U -MemberDefinition @"
[DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, string l);
[DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, System.Text.StringBuilder l);
[DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
[DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
[DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,int e);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")] public static extern int GetSystemMetrics(int i);
"@
function Hwnd($e){ [IntPtr]$e.Current.NativeWindowHandle }
function Show-Point($e){ try { if($A.point_file){ $r=$e.Current.BoundingRectangle; if(-not $r.IsEmpty){ [IO.File]::WriteAllText([string]$A.point_file, ('{0},{1},{2}' -f [int]($r.X+$r.Width/2),[int]($r.Y+$r.Height/2),[W.U]::GetSystemMetrics(0))); Start-Sleep -Milliseconds 550 } } } catch {} }
function Try-Focus($e){ try { $e.SetFocus(); return $true } catch { try { $h=Hwnd $e; if($h -ne [IntPtr]::Zero){ return [W.U]::SetForegroundWindow($h) } } catch {}; return $false } }
function Out($o){ '<<<JSON' ; ($o | ConvertTo-Json -Compress -Depth 6) }
try {
__ACTION__
} catch { Out @{error=$_.Exception.Message}; exit 1 }
'''

ACTIONS = {
    "inspect_windows": r'''
  $l=@(); foreach($w in $root.FindAll($TS::Children,$TRUE_COND)){ if($w.Current.Name){ $l+=Info $w }; if($l.Count -ge $A.limit){break} }
  Out @{windows=$l}''',
    "inspect_descendants": r'''
  $e=Find-Target $A.target; $l=@(); foreach($d in $e.FindAll($TS::Descendants,$TRUE_COND)){ if($d.Current.Name -or $d.Current.AutomationId){ $l+=Info $d }; if($l.Count -ge $A.limit){break} }
  Out @{target=(Info $e); elements=$l}''',
    "inspect": r'''
  Out @{target=(Info (Find-Target $A.target))}''',
    "focus": r'''
  $e=Find-Target $A.target
  $wp=$null; if($e.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$wp)){ if($wp.Current.WindowVisualState -eq 'Minimized'){ $wp.SetWindowVisualState('Normal') } }
  if(-not (Try-Focus $e)){ throw 'Windows refused to give this window the focus (another program is in front and focus stealing is blocked). Click the window once, or use ui_type_text / ui_click which do not need focus.' }
  Out @{focused=(Info $e)}''',
    "click": r'''
  $e=Find-Target $A.target; Show-Point $e; $p=$null; $how=''
  if($e.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$p)){ $p.Invoke(); $how='invoke' }
  elseif($e.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$p)){ $p.Toggle(); $how='toggle' }
  elseif($e.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$p)){ $p.Select(); $how='select' }
  elseif($e.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$p)){ $p.Expand(); $how='expand' }
  else {
    $r=$e.Current.BoundingRectangle; [W.U]::SetCursorPos([int]($r.X+$r.Width/2),[int]($r.Y+$r.Height/2)) | Out-Null
    [W.U]::mouse_event(2,0,0,0,0); [W.U]::mouse_event(4,0,0,0,0); $how='mouse'
  }
  Out @{clicked=(Info $e); how=$how}''',
    "set_text": r'''
  $e=Find-Target $A.target; Show-Point $e; $p=$null
  if($e.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$p) -and -not $p.Current.IsReadOnly){ $p.SetValue([string]$A.value); $how='value' }
  elseif((Hwnd $e) -ne [IntPtr]::Zero -and $e.Current.ClassName -match 'Edit|RichEdit|TextBox'){ [W.U]::SendMessage((Hwnd $e),0x000C,[IntPtr]::Zero,[string]$A.value) | Out-Null; $how='window message' }
  elseif(Try-Focus $e){ Start-Sleep -Milliseconds 150; [System.Windows.Forms.Clipboard]::SetText([string]$A.value); [System.Windows.Forms.SendKeys]::SendWait('^a^v'); $how='paste' }
  else { throw 'this control accepts no direct text and Windows refused the focus needed to paste. Click the window once and retry.' }
  Out @{set=(Info $e); how=$how; chars=([string]$A.value).Length}''',
    "send_keys": r'''
  if($A.target){ if(-not (Try-Focus (Find-Target $A.target))){ throw 'Windows refused to give this window the focus, so keys would go to the wrong program. Click the window once, or use ui_type_text.' }; Start-Sleep -Milliseconds 150 }
  [System.Windows.Forms.SendKeys]::SendWait([string]$A.keys); Out @{sent=$true}''',
    "read": r'''
  $e=Find-Target $A.target; $p=$null; $t=$null
  if($e.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$p)){ $t=$p.Current.Value }
  if(-not $t -and $e.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$p)){ $t=$p.DocumentRange.GetText(20000) }
  if(-not $t -and (Hwnd $e) -ne [IntPtr]::Zero){ $n=[int][W.U]::SendMessage((Hwnd $e),0x000E,[IntPtr]::Zero,[IntPtr]::Zero); if($n -gt 0){ $sb=New-Object System.Text.StringBuilder ([Math]::Min($n,20000)+1); [W.U]::SendMessage((Hwnd $e),0x000D,[IntPtr]$sb.Capacity,$sb) | Out-Null; $t=$sb.ToString() } }
  if(-not $t){ $t=$e.Current.Name }
  Out @{text=[string]$t; target=(Info $e)}''',
    "wait": r'''
  $deadline=(Get-Date).AddMilliseconds($A.timeout_ms); $found=$null
  while((Get-Date) -lt $deadline){ try { $found=Find-Target $A.target; break } catch { Start-Sleep -Milliseconds 300 } }
  if($found){ Out @{appeared=$true; target=(Info $found)} } else { Out @{appeared=$false} }''',
    "screenshot": r'''
  if($A.target){ $r=(Find-Target $A.target).Current.BoundingRectangle; $x=[int]$r.X;$y=[int]$r.Y;$w=[int]$r.Width;$h=[int]$r.Height }
  else { $v=[System.Windows.Forms.SystemInformation]::VirtualScreen; $x=$v.X;$y=$v.Y;$w=$v.Width;$h=$v.Height }
  if($w -le 0 -or $h -le 0){ throw 'the target has no visible area (minimized?)' }
  $bmp=New-Object System.Drawing.Bitmap $w,$h; $g=[System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($x,$y,0,0,$bmp.Size)
  $dir=Split-Path -Parent $A.path; if($dir -and -not (Test-Path $dir)){ New-Item -ItemType Directory -Force $dir | Out-Null }
  $bmp.Save($A.path,[System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose()
  Out @{path=$A.path; width=$w; height=$h}''',
}

NAMED = {"ENTER": "{ENTER}", "RETURN": "{ENTER}", "TAB": "{TAB}", "ESC": "{ESC}", "ESCAPE": "{ESC}", "BACKSPACE": "{BACKSPACE}", "DELETE": "{DELETE}",
         "DEL": "{DELETE}", "UP": "{UP}", "DOWN": "{DOWN}", "LEFT": "{LEFT}", "RIGHT": "{RIGHT}", "HOME": "{HOME}", "END": "{END}",
         "PGUP": "{PGUP}", "PAGEUP": "{PGUP}", "PGDN": "{PGDN}", "PAGEDOWN": "{PGDN}", "SPACE": " ", "INSERT": "{INSERT}",
         **{f"F{i}": f"{{F{i}}}" for i in range(1, 13)}}
MODS = {"CTRL": "^", "CONTROL": "^", "ALT": "%", "SHIFT": "+"}


def to_sendkeys(keys: str) -> str:
    """'CTRL+S' -> '^s', 'ALT+F4' -> '%{F4}', 'ENTER' -> '{ENTER}', anything else = literal text (escaped)."""
    k = keys.strip()
    if k.upper() in NAMED:
        return NAMED[k.upper()]
    parts = [p.strip() for p in k.split("+")]
    if len(parts) > 1 and all(p.upper() in MODS for p in parts[:-1]) and parts[-1]:
        last = parts[-1]
        return "".join(MODS[p.upper()] for p in parts[:-1]) + (NAMED.get(last.upper()) or _literal(last.lower()))
    return _literal(keys)


def _literal(text: str) -> str:
    return "".join("{" + ch + "}" if ch in "+^%~(){}[]" else ch for ch in text)


def build_script(action: str, args: dict) -> str:
    payload = base64.b64encode(json.dumps(args, ensure_ascii=False).encode("utf-8")).decode()
    return PRELUDE.replace("__ARGS__", payload).replace("__ACTION__", ACTIONS[action])


def parse_output(text: str) -> dict | None:
    marker = text.rfind("<<<JSON")
    if marker < 0:
        return None
    try:
        return json.loads(text[marker + 7:].strip().splitlines()[0])
    except (ValueError, IndexError):
        return None
