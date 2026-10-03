# Data model

Normalized records keep collectors, integrations, and rendering independent. Source and symbol records are immutable snapshots; run records aggregate the measurements captured in one session.

## BackendCapabilities

:: linescope.model:BackendCapabilities
    :: signature
    :: head
    :: table:
        - attributes

## SourceLocation

:: linescope.model:SourceLocation
    :: signature
    :: head
    :: table:
        - attributes

## SourceUnit

:: linescope.model:SourceUnit
    :: signature
    :: head
    :: table:
        - attributes

## SymbolDefinition

:: linescope.model:SymbolDefinition
    :: signature
    :: head
    :: table:
        - attributes

## SymbolRef

:: linescope.model:SymbolRef
    :: signature
    :: head
    :: table:
        - attributes

## MemoryStats

:: linescope.model:MemoryStats
    :: signature
    :: head
    :: table:
        - attributes

## LineStats

:: linescope.model:LineStats
    :: signature
    :: head
    :: table:
        - attributes

## FunctionStats

:: linescope.model:FunctionStats
    :: signature
    :: head
    :: table:
        - attributes

## SparkExecutionStats

:: linescope.model:SparkExecutionStats
    :: signature
    :: head
    :: table:
        - attributes

## SparkOperator

:: linescope.model:SparkOperator
    :: signature
    :: head
    :: table:
        - attributes

## SparkExecution

:: linescope.model:SparkExecution
    :: signature
    :: head
    :: table:
        - attributes

## ProfileRun

:: linescope.model:ProfileRun
    :: signature
    :: head
    :: table:
        - attributes

## ProfileResult

:: linescope.model:ProfileResult
    :: signature
    :: head
    :: table:
        - attributes
