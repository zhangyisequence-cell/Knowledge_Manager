$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$fixtureDir = Join-Path $projectRoot 'tests/fixtures'
$fixturePath = Join-Path $fixtureDir 'legacy.doc'
$evidenceDir = Join-Path $projectRoot 'runtime/samples'
$evidencePath = Join-Path $evidenceDir 'legacy-word-fixture.json'
$firstParagraph = '旧版文档验证：保留原始资料，负责人李明。'
$lastParagraph = '待办：周五完成归档核对。'
$expectedCells = @(
    @('项目', '金额', '截止日期'),
    @('资料采购', '12800', '2026-09-30')
)
$personalProperties = [ordered]@{ author = 3; last_author = 7; manager = 20; company = 21 }
$metadataPropertyAccess = [ordered]@{ author = $false; last_author = $false; manager = $false; company = $false }
$metadataChecks = [ordered]@{
    remove_all_document_information_succeeded = $false
    remove_personal_information_enabled = $false
}

function Release-OwnedComObject($Value) {
    if ($null -ne $Value -and [System.Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value) | Out-Null
    }
}

New-Item -ItemType Directory -Force -Path $fixtureDir | Out-Null
New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
$word = $null
$documents = $null
$document = $null
$readDocument = $null
$table = $null
$readTable = $null
$tables = $null
$readTables = $null
$ownedInstance = $false
$readBackText = $null
$readBackCells = @()

try {
    $word = New-Object -ComObject Word.Application
    $documents = $word.Documents
    if ($documents.Count -ne 0) {
        throw 'The Word COM instance contains existing documents; refusing to use or quit it.'
    }
    $ownedInstance = $true
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3

    $document = $documents.Add()
    $content = $null
    try {
        $content = $document.Content
        $content.Text = $firstParagraph + "`r"
    } finally {
        Release-OwnedComObject $content
    }

    $content = $null
    $insertion = $null
    try {
        $content = $document.Content
        $endPosition = $content.End - 1
        $insertion = $document.Range($endPosition, $endPosition)
        $tables = $document.Tables
        $table = $tables.Add($insertion, 2, 3)
    } finally {
        Release-OwnedComObject $insertion
        Release-OwnedComObject $content
    }
    for ($row = 1; $row -le 2; $row++) {
        for ($column = 1; $column -le 3; $column++) {
            $cell = $null
            $cellRange = $null
            try {
                $cell = $table.Cell($row, $column)
                $cellRange = $cell.Range
                $cellRange.Text = $expectedCells[$row - 1][$column - 1]
            } finally {
                Release-OwnedComObject $cellRange
                Release-OwnedComObject $cell
            }
        }
    }
    Release-OwnedComObject $table
    $table = $null
    Release-OwnedComObject $tables
    $tables = $null

    $content = $null
    $insertion = $null
    try {
        $content = $document.Content
        $endPosition = $content.End - 1
        $insertion = $document.Range($endPosition, $endPosition)
        $insertion.InsertAfter($lastParagraph)
    } finally {
        Release-OwnedComObject $insertion
        Release-OwnedComObject $content
    }

    # Only this synthetic document is changed; Word's global identity stays untouched.
    $document.RemovePersonalInformation = $true
    $document.RemoveDocumentInformation(99) # wdRDIAll
    $metadataChecks.remove_all_document_information_succeeded = $true
    $document.RemovePersonalInformation = $true

    # wdFormatDocument = 0: the binary Word 97-2003 format.
    $document.SaveAs2($fixturePath, 0)
    $document.RemovePersonalInformation = $true
    $document.Save()
    $savedFormat = [int]$document.SaveFormat
    if ($savedFormat -ne 0) {
        throw "Word returned unexpected SaveFormat $savedFormat."
    }
    $document.Close(0)
    Release-OwnedComObject $document
    $document = $null

    $fixtureBytes = [System.IO.File]::ReadAllBytes($fixturePath)
    if ($fixtureBytes.Length -lt 8) {
        throw 'Word generated an empty or truncated DOC fixture.'
    }
    $headerHex = [BitConverter]::ToString($fixtureBytes[0..7]).Replace('-', ' ')
    if ($headerHex -ne 'D0 CF 11 E0 A1 B1 1A E1') {
        throw "Generated DOC does not have the OLE compound-file signature: $headerHex"
    }

    # This opens only the synthetic fixture saved above, with macros disabled.
    $readDocument = $documents.Open($fixturePath, $false, $true, $false)
    $metadataChecks.remove_personal_information_enabled = [bool]$readDocument.RemovePersonalInformation
    $properties = $null
    try {
        $properties = $readDocument.BuiltInDocumentProperties
        foreach ($entry in $personalProperties.GetEnumerator()) {
            $property = $null
            $propertyValue = $null
            $metadataAccessStage = 'property lookup'
            try {
                $property = $properties.GetType().InvokeMember('Item', [System.Reflection.BindingFlags]::GetProperty, $null, $properties, @([int]$entry.Value))
                if ($null -eq $property) {
                    throw 'Synthetic document property lookup returned no property object.'
                }
                $metadataAccessStage = 'value lookup'
                $propertyValue = [string]($property.GetType().InvokeMember('Value', [System.Reflection.BindingFlags]::GetProperty, $null, $property, $null))
                $metadataAccessStage = 'value normalization'
                $propertyValue = $propertyValue.Trim()
                # These are Office's anonymous author labels, never captured values.
                $isAnonymousAuthor = $entry.Key -in @('author', 'last_author') -and $propertyValue -in @('Author', '作者')
                $isSanitized = [string]::IsNullOrWhiteSpace($propertyValue) -or $isAnonymousAuthor
                $metadataChecks[($entry.Key + '_is_empty_or_anonymous')] = [bool]$isSanitized
                $metadataPropertyAccess[$entry.Key] = $true
            } catch {
                # An unavailable COM property is unknown, never a successful cleanup check.
                $metadataPropertyAccess[$entry.Key] = $false
            } finally {
                $propertyValue = $null
                Release-OwnedComObject $property
            }
        }
    } finally {
        Release-OwnedComObject $properties
    }
    $content = $null
    try {
        $content = $readDocument.Content
        $readBackText = $content.Text
    } finally {
        Release-OwnedComObject $content
    }
    if (-not $readBackText.Contains($firstParagraph) -or -not $readBackText.Contains($lastParagraph)) {
        throw 'Chinese paragraph readback did not match the synthetic source.'
    }
    $readTables = $readDocument.Tables
    if ($readTables.Count -ne 1) {
        throw "Expected exactly one table; found $($readTables.Count)."
    }
    $readTable = $readTables.Item(1)
    for ($row = 1; $row -le 2; $row++) {
        $rowValues = @()
        for ($column = 1; $column -le 3; $column++) {
            $cell = $null
            $cellRange = $null
            try {
                $cell = $readTable.Cell($row, $column)
                $cellRange = $cell.Range
                $cellText = $cellRange.Text.TrimEnd([char[]]@([char]13, [char]7))
                if ($cellText -ne $expectedCells[$row - 1][$column - 1]) {
                    throw "Table cell ($row, $column) readback did not match the synthetic source."
                }
                $rowValues += $cellText
            } finally {
                Release-OwnedComObject $cellRange
                Release-OwnedComObject $cell
            }
        }
        $readBackCells += ,$rowValues
    }
} finally {
    Release-OwnedComObject $readTable
    Release-OwnedComObject $readTables
    Release-OwnedComObject $table
    Release-OwnedComObject $tables
    try {
        if ($null -ne $readDocument) {
            try { $readDocument.Close(0) } finally { Release-OwnedComObject $readDocument }
        }
    } finally {
        try {
            if ($null -ne $document) {
                try { $document.Close(0) } finally { Release-OwnedComObject $document }
            }
        } finally {
            try {
                if ($null -ne $word -and $ownedInstance) {
                    $word.Quit(0)
                }
            } finally {
                Release-OwnedComObject $documents
                Release-OwnedComObject $word
            }
        }
    }
}

$evidence = [ordered]@{
    generated_utc = [DateTime]::UtcNow.ToString('o')
    path = $fixturePath
    format = 'Word 97-2003 binary DOC'
    word_save_format = $savedFormat
    header_hex = $headerHex
    size_bytes = (Get-Item -LiteralPath $fixturePath).Length
    sha256 = (Get-FileHash -LiteralPath $fixturePath -Algorithm SHA256).Hash.ToLowerInvariant()
    readback_verified = $true
    readback_text = $readBackText
    table_cells = $readBackCells
    automation_security = 3
    metadata_cleanup = $metadataChecks
    metadata_property_readback_available = $metadataPropertyAccess
    metadata_property_checks_complete = ($metadataPropertyAccess.Values -notcontains $false)
    antiword_tested = $false
}
$evidenceJson = $evidence | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText($evidencePath, $evidenceJson, [System.Text.UTF8Encoding]::new($false))
$evidenceJson
