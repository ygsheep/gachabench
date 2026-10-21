# -*- mode: powershell -*-
<#
Pick a folder with the real Windows shell dialog.

Uses IFileOpenDialog with FOS_PICKFOLDERS -- the same native "Select Folder"
dialog Explorer shows on Windows 10/11. tkinter's askdirectory() is NOT used as
the primary path because it falls back to SHBrowseForFolder, the legacy tree
dialog from the Win9x era.

NOTE: this file is deliberately pure ASCII. A BOM-less UTF-8 .ps1 is read as
ANSI by Windows PowerShell 5.1, which would garble any non-ASCII comment or
literal. Callers pass localized strings (e.g. the dialog title) as parameters;
Python marshals those as UTF-16 through CreateProcess, so they arrive intact.

Result is written to -OutFile as UTF-8 (no BOM), first line only:
    OK<TAB>absolute\path
    CANCEL
    ERR<TAB>message
Writing to a file instead of stdout avoids the console code page entirely.

Exit codes: 0 ok (including CANCEL), 3 not STA, 4 dialog failed.
#>
[CmdletBinding()]
param(
    [string]$Title = 'Select folder',
    [string]$Initial = '',
    [Parameter(Mandatory = $true)][string]$OutFile
)

$ErrorActionPreference = 'Stop'
$TAB = [char]9

function Write-Result([string]$text) {
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($OutFile, $text, $enc)
}

try {
    if ([System.Threading.Thread]::CurrentThread.GetApartmentState() -ne
        [System.Threading.ApartmentState]::STA) {
        Write-Result ('ERR' + $TAB + 'STA apartment required (-STA)')
        exit 3
    }

    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class GachaFolderPicker
{
    // CLSID_FileOpenDialog
    [ComImport, ClassInterface(ClassInterfaceType.None),
     Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
    private class FileOpenDialogRCW { }

    [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IFileDialog
    {
        [PreserveSig] int Show(IntPtr parent);
        void SetFileTypes(uint cFileTypes, IntPtr rgFilterSpec);
        void SetFileTypeIndex(uint iFileType);
        void GetFileTypeIndex(out uint piFileType);
        void Advise(IntPtr pfde, out uint pdwCookie);
        void Unadvise(uint dwCookie);
        void SetOptions(uint fos);
        void GetOptions(out uint pfos);
        void SetDefaultFolder(IShellItem psi);
        void SetFolder(IShellItem psi);
        void GetFolder(out IShellItem ppsi);
        void GetCurrentSelection(out IShellItem ppsi);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
        void GetResult(out IShellItem ppsi);
        void AddPlace(IShellItem psi, int fdap);
        void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string pszDefaultExtension);
        void Close([MarshalAs(UnmanagedType.Error)] int hr);
        void SetClientGuid(ref Guid guid);
        void ClearClientData();
        void SetFilter(IntPtr pFilter);
    }

    [ComImport, Guid("d57c7288-d4ad-4768-be02-9d969532d960"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IFileOpenDialog
    {
        // IFileDialog members must be re-declared verbatim: COM inheritance is a
        // vtable layout, and C# needs the whole thing in declaration order.
        [PreserveSig] int Show(IntPtr parent);
        void SetFileTypes(uint cFileTypes, IntPtr rgFilterSpec);
        void SetFileTypeIndex(uint iFileType);
        void GetFileTypeIndex(out uint piFileType);
        void Advise(IntPtr pfde, out uint pdwCookie);
        void Unadvise(uint dwCookie);
        void SetOptions(uint fos);
        void GetOptions(out uint pfos);
        void SetDefaultFolder(IShellItem psi);
        void SetFolder(IShellItem psi);
        void GetFolder(out IShellItem ppsi);
        void GetCurrentSelection(out IShellItem ppsi);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
        void GetResult(out IShellItem ppsi);
        void AddPlace(IShellItem psi, int fdap);
        void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string pszDefaultExtension);
        void Close([MarshalAs(UnmanagedType.Error)] int hr);
        void SetClientGuid(ref Guid guid);
        void ClearClientData();
        void SetFilter(IntPtr pFilter);
        // ---- IFileOpenDialog proper ----
        void GetResults(out IntPtr ppenum);
        void GetSelectedItems(out IntPtr ppsel);
    }

    [ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellItem
    {
        void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
        void GetParent(out IShellItem ppsi);
        void GetDisplayName(uint sigdnName,
                            [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
        void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
        void Compare(IShellItem psi, uint hint, out int piOrder);
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, ExactSpelling = true,
               PreserveSig = true)]
    private static extern int SHCreateItemFromParsingName(
        [MarshalAs(UnmanagedType.LPWStr)] string pszPath,
        IntPtr pbc,
        ref Guid riid,
        [MarshalAs(UnmanagedType.Interface)] out IShellItem ppv);

    private const uint FOS_PICKFOLDERS     = 0x00000020;
    private const uint FOS_FORCEFILESYSTEM = 0x00000040;
    private const uint FOS_PATHMUSTEXIST   = 0x00000800;
    private const uint SIGDN_FILESYSPATH   = 0x80058000;
    private const int  HRESULT_CANCELLED   = unchecked((int)0x800704C7);

    /// Returns null when the user cancels. 'initial' only seeds the location and
    /// is ignored when it cannot be resolved (e.g. the folder no longer exists).
    public static string Pick(string title, string initial)
    {
        var dlg = (IFileOpenDialog)new FileOpenDialogRCW();
        dlg.SetOptions(FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST);

        if (!string.IsNullOrEmpty(title))
        {
            try { dlg.SetTitle(title); } catch { }
        }

        if (!string.IsNullOrEmpty(initial))
        {
            try
            {
                Guid iid = typeof(IShellItem).GUID;
                IShellItem start;
                if (SHCreateItemFromParsingName(initial, IntPtr.Zero, ref iid, out start) == 0
                    && start != null)
                {
                    dlg.SetFolder(start);
                }
            }
            catch { }
        }

        int hr = dlg.Show(IntPtr.Zero);
        if (hr == HRESULT_CANCELLED) return null;
        if (hr != 0) throw new COMException("dialog returned 0x" + hr.ToString("X8"), hr);

        IShellItem item;
        dlg.GetResult(out item);
        if (item == null) return null;

        string path;
        item.GetDisplayName(SIGDN_FILESYSPATH, out path);
        return path;
    }
}
'@

    $path = [GachaFolderPicker]::Pick($Title, $Initial)
    if ([string]::IsNullOrEmpty($path)) {
        Write-Result 'CANCEL'
    } else {
        Write-Result ('OK' + $TAB + $path)
    }
    exit 0
}
catch {
    Write-Result ('ERR' + $TAB + $_.Exception.Message)
    exit 4
}
